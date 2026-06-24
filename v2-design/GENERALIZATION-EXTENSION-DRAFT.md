# ChaosProbe — Generalization / external-validity extension — pre-registration (**DRAFT, NOT FROZEN**)

> **Status: DRAFT — not yet binding.** This document specifies an *extension*
> study that discharges the **standing threats to validity** of the thesis — the
> ones the availability-axis construction fix (`AX-PREREGISTRATION-DRAFT.md`)
> does *not* touch: single cluster, single confirmatory workload, ipvs-only
> kube-proxy mode, and the conntrack drop being a *proxy* with no causal
> decomposition. Like the AX draft it becomes binding only after its TBDs are
> filled, a freeze commit + tag, and (if you choose to deposit) an archive.

## 0. Honest framing (binding on how results are reported)

These are **scope boundaries, not design flaws.** This extension *discharges the
named threats* by widening the validity space; it does **not** make the thesis
"limitation-free," and no design can. After it runs, the thesis is bounded by
*the specific* environments, workloads, and proxy modes tested here instead of by
one of each — a smaller, explicitly-stated bound, not zero. Any report of this
work states that plainly. The construction-limit chapter (availability axis) is
closed by the AX study; this study closes the *external-validity* chapter to the
extent any finite study can.

**Resource and safety prerequisites (binding).**
- A **second, legitimate test environment** is required for Arm G1 (a managed
  cloud cluster, a bare-metal cluster, or the same hardware reconfigured to a
  different kube-proxy mode). It must be a disposable test cluster.
- ⛔ **The corporate production AKS cluster must never be a target.** Every chaos
  command verifies `KUBECONFIG` and `kubectl config current-context` against the
  intended test cluster first (the standing safety gate). No exceptions.
- Each arm is multi-day compute; the arms are independent and can be run, frozen,
  and deposited separately.

---

## 1. Threats discharged, and by which arm

| Standing threat (Ch. threats) | Addressed by | Extent |
|---|---|---|
| Single small virtualized cluster (one environment) | **G1** second-infrastructure replication | direction only |
| ipvs only; H6 iptables direction-transfer never run | **G1** (a kube-proxy-mode arm *is* the H6 comparison) | direction only |
| Single confirmatory workload (online-boutique) | **G2** additional confirmatory workload | verdict transfer |
| Conntrack drop is a *proxy*; TCP-vs-UDP apportionment pending | **G3** conntrack-composition probe | partial — mechanism-level decomposition only |
| Multi-replica / HA failover beyond node-drain r=3 | **G4** (named, unspecified — §4a) | future arm |

"Addressed" means the named threat is *reduced to the tested pair/set*, not
eliminated — see the per-arm extents above and §6. G1--G3 below are
self-contained pre-registered sub-studies, each with its own confirmatory
question, frozen criteria, and not-runnable hatch; G4 is named but deliberately
left unspecified (§4a).

---

## 2. Arm G1 — Second-environment direction transfer (discharges single-cluster + ipvs-only + H6)

**Statement.** The **direction** of the thesis's two reproducible effects
transfers to a second environment that differs in the dimension most likely to
matter: (a) the placement-dependence of the during-churn conntrack drop (H2's
cache-off arm), and (b) the placement-dependence of the node-drain availability
trough (AX-H1). Magnitudes are environment-specific and **not** registered;
**direction only** is the transferable claim (the same scope the original H6 set).

**Design.** Re-run the minimal endpoint cells — packed ($f{=}0$) vs spread
($f{=}1$) for the conntrack arm, and the $f$-sweep for the availability arm — on a
**second environment** chosen from: (i) a different **kube-proxy mode**
(iptables or nftables instead of ipvs — this cell *is* the de-scoped H6), and/or
(ii) **different infrastructure** (a managed cloud cluster or bare metal).
**[TBD: which second environment(s) — see §5.]** Same solver, same fault
scenarios, same probers; the cluster fingerprint is archived per run.

**Test (per effect).** Sign test on the direction across **≥5 sessions** per
endpoint cell (matching the original H6's registered test). No magnitude
prediction. Reported as: direction preserved / not preserved / not-attempted.

**Conntrack caveat (binding).** Conntrack behaviour changed materially across
Kubernetes v1.31--v1.32; the second environment's K8s version, kube-proxy mode,
and conntrack settings are archived, and the direction claim is scoped to the
tested pair, not "Kubernetes broadly."

**Not-runnable hatch.** If no second environment is available by the registered
deadline, G1 is recorded **not-attempted** (as H6 was), not silently dropped.

---

## 3. Arm G2 — Second confirmatory workload (discharges single-workload)

**Statement.** The confirmatory family's *verdicts* (not magnitudes) reproduce on
a structurally different workload, pre-registered **before** re-collection.

**Design (binding integrity constraints).** A **fresh, pre-registered
re-collection** is mandatory — the existing hotelReservation campaign **stays
exploratory and is never relabeled confirmatory** (its hypotheses/SESOIs were
already evaluated against its outcomes, so re-using it would be HARKing, exactly
as AX-H2 forbids re-using C2 with a post-hoc margin). The confirmatory family's
hypotheses, SESOIs, and per-cell $n$ are frozen **blind to the existing hotel
results**: they are carried **verbatim from the online-boutique registered bars**,
or set from a **fresh hotel A/A block** — *never* re-derived from the existing
hotel campaign's outcomes. Workload choice: a fresh hotelReservation collection,
**or** a third workload (e.g. Sock Shop / Train Ticket). **[TBD:
fresh-hotel vs third-workload — §5.]** The frozen confirmatory family is H1
(latency dose-response) and AX-H1/AX-H2 (the corrected availability axis); H2
(conntrack) is included where the workload's protocol mix supports it.

**Test.** The online-boutique primary tests and registered bars, applied to the
fresh second-workload data, Holm-corrected within the second-workload family.
Verdict transfer is the registered outcome; cross-workload magnitude differences
are descriptive.

**Not-runnable hatch.** If neither a fresh hotel collection nor a third workload
is feasible by the registered deadline, G2 is recorded **not-attempted**; the
existing hotel arm remains exploratory external validity as reported in the
thesis.

**Honesty.** Two workloads is still a finite set; the claim becomes "holds on
online-boutique and workload X," never "holds on microservices broadly."

---

## 4. Arm G3 — Conntrack-composition probe (discharges the H2 proxy/construct limit)

**Statement.** The during-churn conntrack drop apportions into a **kernel TCP
teardown** component and a **kube-proxy UDP/DNS cleanup** component, and the
*placement-dependent* part is the UDP/DNS pool (converting H2's mechanism from
*consistent* to *causally decomposed*).

**Design.** A **steady-state, multi-iteration protocol-composition probe**
samples conntrack-table composition by protocol/state at high cadence across the
kill cycle (the sampler already exists for the H2 probe; this arm raises its
iteration count and adds the apportionment analysis). Cells: packed ($f{=}0$) and
spread ($f{=}1$), cache-off, $r{=}1$, \texttt{pod-delete}.

**Test (pre-declared).** The drop is decomposed into TCP-state vs UDP-state
deltas per kill cycle. Registered support is a **conjunction**: (a) the
**UDP/DNS** component differs by placement (spread vs packed) at the registered
significance, **and** (b) the **TCP** component is **equivalent** across placement
by a **TOST** test against an A/A-derived band (a registered null requires an
equivalence bar, not mere non-rejection — matching the packing-control TOST
discipline used in the AX and original pre-registrations). This is a
within-mechanism decomposition, not a new user-visible claim.

**Not-runnable hatch.** If the sampler cannot resolve protocol/state at the
cadence the kill cycle requires (verified in a pilot block), the apportionment is
reported as **infeasible at this resolution** rather than forced.

---

## 4a. Arm G4 — Multi-replica / HA failover (named, deliberately unspecified)

G4 is **named but not specified** here. Extending beyond the node-drain $r{=}3$
regime AX-H2 reaches — into broader HA-failover behaviour (multi-replica rolling
churn, PodDisruptionBudgets, multi-node-failure, anti-affinity at scale) — is a
substantially larger study with its own pilots and is **out of scope of this
extension**. It is listed so the standing multi-replica threat is acknowledged,
not silently dropped; it does **not** count toward what this extension discharges
until and unless it is given a full pre-registered specification of its own. The
thesis continues to report multi-replica/HA beyond node-drain as a stated scope
boundary (Ch.~threats), not as addressed.

---

## 5. Open TBDs to fill before freeze

1. **G1 second environment(s):** which of {iptables/nftables mode on the same
   hardware; a managed cloud cluster; bare metal}. At least the kube-proxy-mode
   cell is cheap (reconfigure the existing cluster) and discharges ipvs-only + H6
   on its own.
2. **G1 per-cell n** and the A/A noise floor on the second environment (a fresh
   A/A block is required there — the existing bands are ipvs-specific).
3. **G2 promote-hotel vs add-third-workload**, and the per-cell n / SESOI
   re-calibration for the chosen workload.
4. **G3 sampler cadence + apportionment threshold**, from a feasibility pilot.
5. **Deposit choice** per arm (archive vs available-on-request), consistent with
   the thesis's current self-contained provenance.

---

## 6. What remains bounded even after all arms (stated up front)

Running G1--G3 (and G4, if it is later given its own pre-registration) addresses
the *named* threats but leaves residual, smaller bounds, which the thesis will
state:
- generalization holds across *the tested* environments / workloads / proxy
  modes, not universally;
- the cluster scale (small) is unchanged unless a large-cluster arm is added;
- production traffic patterns and long-horizon / cascading faults remain out of
  scope;
- causal claims remain mechanism-level (G3 decomposes, it does not prove a
  user-visible causal chain).

This residue is the honest floor: a bounded empirical study with *disclosed*
scope, which is the correct end state — not a limitation-free thesis.

## 7. Freeze + deposit procedure

Per arm: run its A/A / feasibility pilot, fill its §5 TBDs, freeze (commit + tag),
optionally archive, then collect. Arms are independent and need not be frozen
together. Mirrors `AX-PREREGISTRATION-DRAFT.md` §8.

## 8. Deviations policy

After each arm's freeze, every deviation is logged with date, reason, and whether
it was decided blind to outcome data — identical to the other pre-registrations.
