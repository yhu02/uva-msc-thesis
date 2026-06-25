# C2 report — replication rescue under node-drain, **hotelReservation** (external validity, exploratory)

**Scope.** Exploratory **external-validity** replication of C2 / **H3**
(replication rescue) on DeathStarBench **hotelReservation** (deep gRPC fan-out,
per-service datastore pairs, Consul discovery). Reported **outside the frozen
confirmatory Holm family**; it adjusts no confirmatory verdict and does not bear
on H1 (C1-hotel) or the conntrack mechanism.

**Provenance.** Data: 24 node-drain sessions (3 cells × 8), hotelReservation,
collected 2026-06-21/22 on `main` commit `bdf1ccb` (all 24 sessions
`runMetadata.git.dirty = false`). Frozen pre-analysis manifest:
`c1-c2-hotel-manifest.sha256`; deposited DOI
[10.5281/zenodo.20792129](https://doi.org/10.5281/zenodo.20792129). All 24
sessions `doctor --strict` clean (0 errors), 0 tainted iterations (each session
is 1 condition × 1 iteration).

## Campaign as run

3 cells × 8 replicate sessions: **r1-packed**, **r3-packed**, **r3-anti-affine**,
each at the single condition `f = 0.5`, 1 iteration, `node-drain` fault,
`--packed-assignment round-robin` (the OB C2 instrument, deviation
D-2026-06-16-01 — round-robin spreads r=3 across the 8 workers so the f=0.5
placement is feasible at hotel's service count). Gate flags as in C1-hotel
(wget-capable probe pod #322 + sustained warm-up). solver-seed 0, order-seed 1.

## H3 — replication rescue under node-drain

Registered test (`01-PREREGISTRATION.md` §H3): an **ART ANOVA** with factors
`r ∈ {1, 3}` × `mode ∈ {packed, anti-affine}`; the registered effect is the
**interaction** — replication rescues availability only when replicas do not
share the failure domain (r=3 anti-affine ≪ r=1; r=3 packed ≈ r=1). Two
co-primary outcomes, **both must pass**. Depth margin for hotel = **0.0526**
(the 1.0-pod M2 noise band expressed as a fraction of the r=1 app-ready
baseline, deviation D-2026-06-15-01; ≈ 1 ÷ 19 services for hotel vs ≈ 0.09 for
online-boutique).

| co-primary | r1 | r3-packed | r3-anti | interaction p | rescue (r1−anti) vs margin | packing TOST |
|---|---|---|---|---|---|---|
| trough depth (frac) | 0.158 | 0.158 | 0.114 | **0.0** (sig) | 0.044 < 0.0526 → **not met** | within band ✓ |
| user error rate | 0.212 | 0.061 | 0.0 | **0.0** (sig) | 0.212 < 0.302 → **not met** | within band ✓ |

Trough **duration** median (s): r1 = 45.0, r3-packed = 30.0, r3-anti = 15.0.

**Outcome: the registered conjunction is NOT met (`CONJUNCTION = False`).** Both
co-primaries show a **significant r×mode interaction** and anti-affine r=3 is
**directionally the best** arm on every measure (lowest trough depth, lowest user
error, shortest trough — a 3× faster recovery than r1: 15 s vs 45 s), and both
**packing controls pass** (r3-packed ≈ r1, within the equivalence band). But on
**both** co-primaries the rescue magnitude falls **short of the registered
margin** — depth rescue 0.044 < 0.053, error rescue 0.212 < 0.302 — so the
both-must-pass conjunction fails.

**Reading the verdict (external validity).** This **replicates the
online-boutique C2 result almost exactly**: same `CONJUNCTION = False`, same
shape — a real, significant interaction with anti-affine directionally rescuing
availability, but not by the pre-registered margin, and the packing controls
holding. Across two structurally different workloads the *mechanism* is robust
and visible (spreading replicas across failure domains demonstrably shrinks the
node-drain blast radius and speeds recovery) while the *registered confirmatory
claim* — rescue clearing the pre-set margin — does not hold on either. The
honest external-validity reading: **the directional replication-rescue effect
generalizes; the strong margin-clearing claim does not.**

**On the margins.** As in OB, the depth-rescue margin is unforgivingly tight for
this fault: under node-drain the r=1 trough is shallow by construction (≈ 0.16
fraction), so the 0.0526-fraction rescue margin leaves little headroom; the
observed 0.044 misses by a single-digit-percent of a pod-equivalent. The user-
error rescue (0.212) is large and the anti-affine arm reaches **0.0** error, but
the 0.302 margin — set from the OB A/A noise band — is not cleared at hotel's
smaller error baseline.

## Limitations

- **Exploratory, not confirmatory.** Outside the Holm family; no multiplicity
  correction, none owed.
- **n = 8 per cell, single iteration per session, single cluster.** Adequate to
  reproduce the OB effect shape but not to tighten the margin question.
- **Round-robin instrument** (D-2026-06-16-01) carried over from OB so the f=0.5
  r=3 placement is feasible at hotel's service count.
- The verdict **mirrors** OB; it does not independently re-open the OB
  pre-registration questions (the depth-margin construction limit is documented
  in C2-OB-REPORT.md and applies identically here).
