# C2 report — replication rescue under node-drain, **online-boutique** (preliminary)

Confirmatory results for **V2-H3** (replication rescue under node-drain),
workload online-boutique. **Preliminary** in the same strict sense as C1: the
registered confirmatory family (V2-H1, V2-H2, V2-H3, V2-H5) is corrected
together by **Holm across all campaigns**, so the per-hypothesis p-values below
are *uncorrected* and final significance waits on the full family. C2 tests
**V2-H3**; it does not bear on V2-H1 (C1 dose-response) or the conntrack
mechanism (V2-H2, C3).

**Provenance.** Data: 24 node-drain sessions (3 cells × 8), online-boutique,
collected on the **round-robin packed instrument** at commit `e533d5b` on a
**strict-clean tree** (`runMetadata.git.dirty = false` in every session;
`archive_run.py --strict` blesses all 24). A SHA-256 manifest of all 48 raw
files (24 `summary.json` + 24 `f-050.json`) was computed **before** analysis and
is committed at
[`c2-roundrobin-manifest.sha256`](c2-roundrobin-manifest.sha256). Pre-registration
frozen at tag `v2-prereg-freeze`, DOI
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
registered in [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §V2-H3
(per-service replica packing; each service's replicas on one node, services
round-robin distributed across nodes) and verified at the M1b gate.

A first C2 attempt (`results/c2-rerun2`) was **discarded**: it ran the packed
cells on the fraction-solver assignment, which at `f=0.50` concentrates 11
services onto ~2 nodes; ×3 replicas = 33 pods on 2 nodes, **unschedulable** on
the 8×(2 CPU / 4 GiB) cluster — all 8 r3-packed sessions there were
placement-rejected. The orchestrator was corrected to use the round-robin
assignment for the V2-H3 cells (PRs #293/#294/#295; deviation **D-2026-06-16-01**),
the driver now excludes rejected/tainted sessions, and this campaign was
collected fresh on the corrected instrument. See [`DEVIATIONS.md`](DEVIATIONS.md)
D-2026-06-16-01.

## V2-H3 — replication rescue under node-drain

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
  of V2-H3: anti-affine `r=3` fully rescues the user-facing error rate (0.0 vs
  r1's 0.632; interaction p ≈ 0; rescue 0.632 ≫ the 0.302 margin).
- **Trough depth** shows a significant interaction (p = 0.0065) but the
  anti-affine rescue (0.0455) falls **below** the 0.0909 margin, so it is **not
  met**.

Because the conjunction requires *both* co-primaries to clear interaction +
rescue + control, and the depth co-primary fails the rescue margin, the
registered V2-H3 conjunction is **not met** on this campaign. The failure is now
isolated to a **single** criterion — the depth-rescue margin — with everything
else (both interactions significant, both packing controls passing, the error
rescue large and significant) supporting V2-H3's direction.

**Methodological caveat (flag for interpretation, not a post-hoc change).**
Under the round-robin placement, draining one node removes the ~1–2 services
pinned to it; for `r=1` that is ≈ 1 pod, so the r1 trough-depth median (0.0909)
is essentially *equal to the registered 1-pod rescue margin* (0.0909). The
depth-rescue criterion (r1−anti ≥ 1 pod) is therefore close to impossible to
satisfy by construction on this placement — the whole app only loses about one
pod's worth of endpoints at r=1, so the anti-affine arm cannot beat r1 by a full
further pod. This is a tension between the registered absolute-1-pod margin and
the round-robin spread, surfaced for the write-up; the analysis is reported
exactly as registered and was **not** re-tuned. (The user-route error face, which
*does* clear its margin, is unaffected by this construction limit.)

## Limitations

- **Preliminary pending Holm** across the confirmatory family (V2-H1/H2/H3/H5);
  final significance of the V2-H3 interaction p-values waits on the full family
  once C3 lands.
- **Single workload.** hotelReservation C2 is not collected; external validity
  across workloads is unestablished.
- **n = 8 per cell, single iteration per session.** The TOST equivalence CIs are
  correspondingly wide; the packed≈r1 control verdicts should be read as
  indicative at this n.
- **Deposited** at DOI
  [10.5281/zenodo.20726729](https://doi.org/10.5281/zenodo.20726729) (raw run,
  before write-up); provenance also anchored by commit `e533d5b`, tag
  `v2-prereg-freeze`, and the committed raw manifest.
- **Depth-margin construction limit** (above): the registered absolute-1-pod
  depth margin is near-unmeetable under the round-robin spread; a reader may
  weigh the depth co-primary's "not met" in that light. The error co-primary
  carries no such limit.
