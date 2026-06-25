# M1a report — solver-feasibility spike: **GO**

> Status: M1a complete (2026-06-11). All four exit criteria met. This report
> is the input to the **M0 hardware decision (user-owned)**. Code: PR #263
> (`6b3a631`); live validation run from the existing 4-worker cluster and
> fully restored afterwards (zero nodeSelectors left, all pods Running).

## Verdict

The project's pre-registered top risk — *can a solver hit the five
cross-node-fraction targets on a real scheduler?* — is **retired**. The
reachable-fraction set was enumerated **exhaustively** (not sampled) at all
three candidate cluster sizes, every target is reachable within the
pre-registered ±0.05, and the live solve→apply→schedule→verify loop at N=4
reproduced the analytical fractions **exactly** (deviation 0.0000 at every
level — far inside the ±0.02 fidelity criterion).

## Exit criteria

| M1a exit criterion (02-WORKPLAN) | Result |
|---|---|
| Unit + property tests in CI, green before any live gate; achieved-fraction validated against an independent second implementation | ✅ exact agreement on all 70 cross-checks (40 random graphs + 30 direct); 100 % line+branch coverage on the new module; suite 2051 green |
| Analytical enumerator matches live achieved-f at N=4 within ±0.02 | ✅ **exact** (Δ = 0.0000 at all five levels; see table) |
| Quantization report (reachable f per N) delivered for M0 | ✅ this document |
| Measured request sums (live earlier cluster, ×3 projection) for the M0 vCPU contingency | ✅ below trigger; see M0 inputs |

## Quantization: reachable fractions per N (exhaustive)

The Online Boutique dependency graph as measured (campaign s07 summary):
**11 services, 15 inter-service edges** → fractions quantize on the k/15
grid — comfortably inside the ±0.05 tolerance at every target.

| N | method | canonical assignments | elapsed | reachable fractions | worst target gap |
|---|---|---|---|---|---|
| 4 | exhaustive (set partitions ≤4 blocks) | 175,275 | 0.18 s | full k/15 grid (16 values) | 0.033 (at f=0.50) |
| 6 | exhaustive (≤6 blocks) | 601,492 | 0.80 s | full k/15 grid (16 values) | 0.033 (at f=0.50) |
| 8 | exhaustive (≤8 blocks) | 677,359 | 0.66 s | full k/15 grid (16 values) | 0.033 (at f=0.50) |

All five pre-registered targets {0, 0.25, 0.50, 0.75, 1.0} are within ±0.05
at every candidate N. **The reachable set does not shrink between N=6 and
N=8**, so the N=6-vs-8 choice is unconstrained by solver reachability.

## Live validation at N=4 (solve → apply → schedule → verify)

Solver placements applied to the live cluster via nodeSelector pinning
(`scripts/apply_placement_map.py`, the earlier mutator's own conventions),
rollouts awaited, achieved fraction recomputed from **live pod placements**
with the same `achieved_fraction` implementation:

| target | analytical achieved-f | live achieved-f | Δ | within ±0.02 |
|---|---|---|---|---|
| 0.00 | 0.0000 | 0.0000 | 0.0000 | ✅ |
| 0.25 | 0.2667 | 0.2667 | 0.0000 | ✅ |
| 0.50 | 0.5333 | 0.5333 | 0.0000 | ✅ |
| 0.75 | 0.7333 | 0.7333 | 0.0000 | ✅ |
| 1.00 | 1.0000 | 1.0000 | 0.0000 | ✅ |

Cluster restored afterwards (0 pinned deployments, 0 non-Running pods).

## M0 inputs (the user-owned hardware decision)

- **Measured request sums (live, r=1):** 1.90 vCPU / 2.55 GiB across the
  namespace's running ReplicaSet-owned pods (conservatively *includes*
  in-namespace infra such as the load generator and chaos operator).
  **r=3 projection: 5.68 vCPU / 7.65 GiB.**
- **vCPU-escalation trigger:** 1.3 × the 1.7 vCPU placeholder = 2.21 vCPU.
  Measured 1.90 < 2.21 → **no escalation: the pinned 6 × 8 GiB × ≥4 vCPU
  spec stands** (no 6-vCPU over-buy needed). Memory at the heaviest cell
  (~7.7 GiB + ~2–3 GiB infra) retains >70 % headroom against ~42 GiB
  allocatable.
- **Reachability does not constrain N** (table above): 6 workers suffice;
  8×4 GiB fallback also reachable but would still require the M1b gate
  re-run at N=8 per the workplan.

**Recommendation: GO — procure the pinned 6 × 8 GiB × ≥4 vCPU cluster as
specified; no contingency triggered.**

## Caveats (carried into M1b / M2)

1. **Edge weights are uniform-1.0** in this graph: the earlier east-west route
   records carry no per-edge call volumes, so `load_dependency_graph` used
   its documented fallback. The *unweighted* and *weighted* fractions
   coincide under uniform weights; if M1b's prober adds real volumes, the
   quantization grid becomes non-uniform and the enumeration must be
   re-run with weights before the M2 freeze (cheap: <1 s).
2. Live validation ran at **r=1 with per-service nodeSelector pinning**
   (the earlier mechanism). The replica-level affinity engine — and hence
   anti-affine r=3 scheduling — is M1b scope, on the pinned-N cluster.
3. The measured graph has **15 inter-service edges** (the ~16 used during
   plan review was an estimate and appears in no design document — no
   sync needed); 15 is now the canonical figure and fixes the quantization
   grid at k/15.
