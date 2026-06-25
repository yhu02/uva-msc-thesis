# M1b report — engine build + live GO/NO-GO gate at N=8: **PASS**

> Status: M1b complete (2026-06-11). The committed verification artifact is
> [`m1b-gate-artifact.json`](m1b-gate-artifact.json) (schema v2, gate run 3).
> Code: PRs #265 (affinity engine + gate script), #266 (conntrack prober),
> #267 (gate-instrument fixes). Cluster: the M0 fallback **N=8 × 4 GiB**
> (1 cp 6 GiB), Kubernetes v1.28.6.

## Verdict

All M1b exit criteria pass on the pinned-N cluster:

| Criterion | Result |
|---|---|
| Solver gate: every f-level ±0.05, ≥3 consecutive attempts | ✅ all 5 levels, 3/3 on the first 3 attempts |
| r=3 **anti-affine** schedulable (every service's replicas on 3 distinct nodes) | ✅ 11/11 services, converged in **53 s** |
| r=3 **packed** control (each service's replicas on exactly 1 node) | ✅ 11/11 services, **43 s** |
| Capacity ≥30 % headroom on both resources at the heaviest cell | ✅ CPU 57 %, memory 79 % |

The anti-affine result is the milestone the whole redesign hinged on: the
capability v1 structurally lacked (replica-level anti-affinity — the reason
E1 was skipped) now demonstrably works at campaign scale.

## Deliverables

- **Affinity engine** (`chaosprobe/placement/affinity_engine.py`, #265):
  r=1 pin / r=3 packed / r=3 anti-affine via required `podAntiAffinity` on
  `kubernetes.io/hostname`; verification reads live pods, never assumes;
  managed-annotation restore. 100 % line+branch coverage.
- **Gate script** (`scripts/m1b_gate.py`, #265 + #267): the pre-registered
  state machine (consecutive counter resets on miss, abort at 6), quiescence
  barriers, capacity-feasible packed assignment (round-robin, recorded as
  `packedAssignmentMethod`), and self-documenting failure diagnostics.
  Artifact schema `chaosprobe/m1b-gate-artifact/v2`.
- **Protocol-labeled conntrack prober** (`chaosprobe/metrics/conntrack.py`,
  #266): first-class collector with standard lifecycle; per-node 5 s
  samples land in `summary.json` (`conntrackProtocolSamples`) phase-aligned
  with chaos windows; `conntrack-tools` **pinned to 1.4.8-r0** and the
  running version recorded per node (`toolVersionsByNode`) — closing the
  M1a I2 finding.

## Gate-instrument history (transparency)

The gate **failed twice before passing**, and both failures were instrument
defects, not design defects — diagnosed by manual reproduction (an identical
`apply_placement(r=3, anti-affine)` on a settled cluster converged in 55 s
with zero restarts while the in-gate attempts timed out):

1. **Packed assignment was unschedulable by construction**: the script used
   the solver's f=0 assignment (all 11 services on one 4 GiB worker → ~7.7 GiB
   of requests on ~3.3 GiB allocatable). C2's "packed" semantics are
   *per-service* packing with services distributed; fixed to round-robin (#267).
2. **No quiescence between churn cycles**: Phase B fired into a cluster still
   digesting ~30 back-to-back Recreate rollouts from Phase A; the nested-virt
   1 s gRPC probe-timeout cascade (the documented v1 capacity signature) kept
   8/11 deployments flapping past even a 600 s budget. Fixed with a
   churn-resetting 60 s quiescence window after every restore (#267).

With the fixes, the previously failing arms pass in 53 s/43 s — confirming
the diagnosis. Failed-run artifacts are preserved off-repo; the committed
artifact is the passing run with the v2 schema (settle records included).

## Pre-freeze amendments (allowed until the M2 freeze)

1. **"Attempt" definition**: implemented as one solve→apply→schedule→verify
   cycle **from a restored (unpinned) state**, not from a full app redeploy.
   Rationale: the scheduling decision under test is identical, and a full
   redeploy per attempt would triple gate duration for no informational
   gain. The artifact records this (`attemptProtocol`). The
   pre-registration's wording will be aligned at the M2 freeze.
2. **Packed-cell semantics**: C2's packed arm = per-service replica packing
   with services round-robin distributed (capacity-feasible), as now
   implemented and verified.

## Carried to M2

- hotelReservation deploy + its solver/capacity gate (M2 prep, per plan).
- A/A calibration sessions (the new prober pipeline's noise
  characterization) → power analysis → **pre-registration freeze**.
- Conntrack prober M2 follow-ups noted in #266: TCP state-class breakdown,
  the pre-window UDP-slope validity check (A/A-band threshold).
- Re-enumerate reachable fractions with real edge weights if the prober's
  volume data lands before the freeze (M1a caveat 1).
