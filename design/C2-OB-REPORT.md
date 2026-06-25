# C2 report — replication rescue under node-drain, **online-boutique** (preliminary)

Confirmatory results for **H3** (replication rescue under node-drain),
workload online-boutique. **Preliminary** in the same strict sense as C1: the
registered confirmatory family (H1, H2, H3, H5) is corrected
together by **Holm across all campaigns**, so the per-hypothesis p-values below
are *uncorrected* and final significance waits on the full family. C2 tests
**H3**; it does not bear on H1 (C1 dose-response) or the conntrack
mechanism (H2, C3).

**Provenance.** Data: 24 node-drain sessions (3 cells × 8), online-boutique,
collected on the **round-robin packed instrument** at commit `e533d5b` on a
**strict-clean tree** (`runMetadata.git.dirty = false` in every session;
`archive_run.py --strict` blesses all 24). A SHA-256 manifest of all 48 raw
files (24 `summary.json` + 24 `f-050.json`) was computed **before** analysis and
is committed at
[`c2-roundrobin-manifest.sha256`](c2-roundrobin-manifest.sha256). Pre-registration
frozen at tag `prereg-freeze`, DOI
[10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836)
([`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md)). Analysis: `scripts/c2_h3_anova.py` at
`e533d5b`. Deposit DOI
[10.5281/zenodo.20726729](https://doi.org/10.5281/zenodo.20726729)
([`C2-OB-DEPOSIT.md`](C2-OB-DEPOSIT.md)) — deposited **before** write-up per
[`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Versioning.

> An earlier collection of this campaign was **discarded and superseded** (it
> recorded `git.dirty = true` from an untracked driver script, which
> `archive_run.py --strict` correctly refused). This report describes the
> strict-clean re-run; the verdict replicated.

## Campaign as run

Between-subjects design: factors `r ∈ {1, 3}` × `mode ∈ {packed, anti-affine}`,
collapsed to the three non-degenerate cells (`r=1` is one baseline shared by both
mode columns) — **r1-packed, r3-packed, r3-anti-affine**, 8 replicate sessions
each (24 total), blocked by cell. Each session: one complete block at the
nominal `f-050` condition, 1 node-drain iteration, host-side Locust on the `/`
user route. All 24 sessions are **accepted and untainted** (`accepted=true`, 0
pending deployments, empty `taintReasons`); none excluded by the registered "no
result from a rejected or fully-tainted session" rule.

## Instrument: round-robin packed assignment (deviation D-2026-06-16-01)

The packed cells use the **capacity-feasible round-robin** packed assignment
registered in [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §H3
(per-service replica packing; each service's replicas on one node, services
round-robin distributed across nodes) and verified at the M1b gate.

A first C2 attempt (`results/c2-rerun2`) was **discarded**: it ran the packed
cells on the fraction-solver assignment, which at `f=0.50` concentrates 11
services onto ~2 nodes; ×3 replicas = 33 pods on 2 nodes, **unschedulable** on
the 8×(2 CPU / 4 GiB) cluster — all 8 r3-packed sessions there were
placement-rejected. The orchestrator was corrected to use the round-robin
assignment for the H3 cells (PRs #293/#294/#295; deviation **D-2026-06-16-01**),
the driver now excludes rejected/tainted sessions, and this campaign was
collected fresh on the corrected instrument. See [`DEVIATIONS.md`](DEVIATIONS.md)
D-2026-06-16-01.

## H3 — replication rescue under node-drain

Registered primary test: **ART ANOVA** with factors `r × mode`; the registered
effect is the **interaction** — replication rescues availability only when
replicas do not share the failure domain (`r=3 anti-affine ≪ r=1`; `r=3 packed
≈ r=1`). Two **co-primary** outcomes, combined **both-must-pass (conjunction)**;
each requires interaction significance **and** the anti-affine rescue margin
**and** the packed≈r1 TOST equivalence control to all hold.

| co-primary | r1 | r3-packed | r3-anti | ART interaction | anti-affine rescue (r1−anti vs margin) | packed≈r1 TOST |
|---|---|---|---|---|---|---|
| **trough depth (fraction)** | 0.0909 | 0.0909 | 0.0455 | sig (p = 0.0065) | 0.0455 < 0.0909 → **not met** | within band ✓ |
| **user-route error rate** | 0.6316 | 0.6316 | 0.0000 | sig (p ≈ 0) | 0.6316 ≥ 0.302 → **MET** ✓ | within band ✓ |

Trough-depth margin 0.0909 = 1.0 pod ÷ the r=1 app baseline (deviation
D-2026-06-15-01, fractional operationalization). Trough-duration medians (s):
r1 = 30.0, r3-packed = 30.0, r3-anti = 30.0.

**Outcome: the registered conjunction is NOT met (`CONJUNCTION = False`)** — and,
unlike the discarded first attempts, this is a substantive result on
strict-clean valid data. The result is now internally consistent across the two
faces:

- **Both packing controls pass.** Packed `r=3` ≈ `r=1` on *both* co-primaries
  (error 0.6316 vs 0.6316 exactly; depth 0.0909 vs 0.0909), so the registered
  "replication does not rescue when replicas share the failure domain" control
  holds — the instrument behaves as designed.
- **User-route error rate** gives strong, significant support for the *direction*
  of H3: anti-affine `r=3` fully rescues the user-facing error rate (0.0 vs
  r1's 0.632; interaction p ≈ 0; rescue 0.632 ≫ the 0.302 margin).
- **Trough depth** shows a significant interaction (p = 0.0065) but the
  anti-affine rescue (0.0455) falls **below** the 0.0909 margin — which, on this
  placement, the depth co-primary **could not have cleared regardless of the
  true effect** (see below).

**Reading the verdict.** The registered conjunction is **not met**, but the
single failing criterion — the trough-depth rescue margin — is one the depth
co-primary was **unable to adjudicate by construction on this placement**, not
evidence against rescue. Under round-robin spread, draining one node costs r=1
only ≈ 1 pod of endpoints in total, so r=1's trough depth (0.0909) ≈ the
registered 1-pod margin: the metric's dynamic range at r=1 is the same size as
the bar the anti-affine arm must beat, leaving no room for a ≥1-pod rescue to
register. **The substantive result is therefore the user-route error face**,
where anti-affinity rescues strongly and significantly (0.632 → 0.0, p ≈ 0,
rescue ≫ margin), with both packing controls passing. Honest summary:
*replication rescues user-visible availability under node-drain when replicas
are spread; the trough-depth face could not adjudicate rescue on this placement,
so the strict both-faces conjunction is not satisfied* — directional support for
H3, not a refutation. (The depth-margin construction limit is a lesson for a
future pre-registration — e.g. a depth margin defined relative to the realized
r=1 depth — not a post-hoc retune of this frozen, analyzed criterion.)

**On the depth-margin construction limit (the precise arithmetic).** Under
round-robin placement, draining one node removes the ~1–2 services pinned to it;
for `r=1` that is ≈ 1 pod, so the r1 trough-depth median (0.0909) is essentially
*equal to the registered 1-pod rescue margin* (0.0909) — the depth metric's
dynamic range at r=1 coincides with the bar. The analysis is reported exactly as
registered and was **not** re-tuned; this is surfaced as a measurement limit on
the depth face, with the error face (which carries no such limit) supplying the
substantive result.

**Exploratory sensitivity (NOT confirmatory — does not change the verdict).**
To show what the depth face indicates once the dynamic-range limit is removed, a
*post-hoc, illustrative* **relative** view (not pre-registered, reported for
transparency only): anti-affine trough depth (0.0455) is **50.1 % of r1's**
(0.0909) — a **~50 % reduction**, with packed r=3 unchanged from r1. So the depth
face *descriptively* shows anti-affinity halving the trough, directionally
consistent with the error face's full rescue; the registered absolute 1-pod
margin simply could not express it. This is exploratory and carries **no
confirmatory weight** — the pre-registered H3 verdict remains `CONJUNCTION =
False` exactly as computed; retuning the frozen margin to flip it would be
p-hacking and is not done.

## Limitations

- **Preliminary pending Holm** across the confirmatory family (H1/H2/H3/H5);
  final significance of the H3 interaction p-values waits on the full family
  once C3 lands.
- **Single workload.** hotelReservation C2 is not collected; external validity
  across workloads is unestablished.
- **n = 8 per cell, single iteration per session.** The TOST equivalence CIs are
  correspondingly wide; the packed≈r1 control verdicts should be read as
  indicative at this n.
- **Deposited** at DOI
  [10.5281/zenodo.20726729](https://doi.org/10.5281/zenodo.20726729) (raw run,
  before write-up); provenance also anchored by commit `e533d5b`, tag
  `prereg-freeze`, and the committed raw manifest.
- **Depth-margin construction limit** (above): the registered absolute-1-pod
  depth margin is near-unmeetable under the round-robin spread; a reader may
  weigh the depth co-primary's "not met" in that light. The error co-primary
  carries no such limit.

## Lesson for future pre-registrations (forward fix — not applied to this frozen study)

The depth-margin construction limit is a **specification** issue, fixable in the
next pre-registration (hotelReservation C2, a v3); it is **not** a data problem
and does **not** require re-running C2 (see below). Two changes carry forward:

1. **Define availability-rescue margins *relatively*, not in absolute pods.**
   Express the trough-depth rescue as a fraction of the realized r=1 depth (e.g.
   "anti-affine depth ≤ X % of r1 depth"), or use a metric with inherent dynamic
   range (integrated outage = depth × duration, or the request-error rate). An
   absolute pod-count margin collides with the small per-node footprint forced by
   a capacity-thin cluster.
2. **Add an achievability check to the freeze checklist.** Every SESOI/margin is
   validated not only against the A/A *noise floor* (the current rule) but also
   against the metric's *achievable range* on the actual placement + cluster — so
   a margin that exceeds the maximum observable effect is caught **before** the
   freeze, not after analysis. (Here: a design-time "max possible r1−anti depth
   under round-robin on N=8" check would have shown range ≈ margin.)
