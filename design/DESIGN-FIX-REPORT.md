# Design-corrected re-analysis of the availability axis (H3/H4/H5) — report

**Scope.** Exploratory, **outside the frozen Holm family**. Three confirmatory
availability tests were construction-limited because `pod-delete` at r=1 cannot
move availability. Criteria were **pre-declared** in `DESIGN-FIX-SCOPE.md` before
the corrected analyses were run. Driver: `chaosprobe/scripts/design_fix_analysis.py`.

**Provenance.** New data: C4 node-drain dose-response, **8 complete-block sessions**
(`results/c4-nodedrain-dose/`), collected 2026-06-22/23 on `main` (`git.dirty=false`;
sessions 1–2 commit `445c5bc`, 3–8 commit `71c545d` — identical run-path code, the
intervening merge was thesis-docs only). All 8 sessions `doctor --strict` clean
(0 errors). H3 re-uses the existing, deposited C2 node-drain data
(`results/c2-roundrobin/`). Pre-analysis manifest: `c4-nodedrain-manifest.sha256`.

## The fault (recap)

`pod-delete` at r=1 removes the only replica of one service → the availability
trough is ≈ 1 pod for every placement, and the depth margin (1 pod) equals the
realized r=1 dynamic range. Hence: H3's depth co-primary un-passable by
construction; H4's availability face degenerate (constant); H5's availability ICC
computed on noise. The remedy is a fault that produces a real, placement-dependent
outage — `node-drain` (its blast radius = the services co-located with the
drained node).

## FIX-H4 — placement frontier with a live availability face

The C4 node-drain dose-response makes the availability axis vary strongly and
monotonically with placement:

| f (packing→spreading) | availability trough (median, n=8) | east-west p95 (ms) |
|---|---|---|
| f=0 (packed) | **1.000** (all app endpoints lost) | 35.5 |
| f=0.25 | 0.818 | 36.3 |
| f=0.50 | 0.636 | 51.3 |
| f=0.75 | 0.636 | 36.2 |
| f=1 (spread) | **0.364** | 41.0 |

The availability face now ranges 1.0→0.364 (vs the degenerate ≈1-pod-everywhere
under pod-delete). A genuine **latency×availability trade-off** appears: spreading
buys a large availability gain (blast radius 1.0→0.36) at a small east-west
latency cost (~35→41 ms, within the sub-SESOI band of H1). The corrected frontier
is non-degenerate and **spreading is the availability-dominant region** — replacing
H4's vacuous "all five non-dominated."

## FIX-H5 — availability sub-score reliability under a fault that produces outage

Computed on the C4 node-drain sessions, the availability sub-score's test-retest
**ICC = 1.0** (between-level variance 0.045, between-session variance *identically*
0) versus **0.180** on the pod-delete C1 data. The pod-delete value reflected
*absent signal*, not an unreliable scorecard.

*Caveat (the honest reading):* the ICC is **1.0 by construction** — the
fraction-solver placement is deterministic (solver-seed fixed), so every session's
blast radius is identical (between-session variance is identically zero) and the
ICC carries no test-retest information. The substantive, defensible claim is the
weaker one: under a fault that produces an outage the availability layer has large,
well-defined signal — not a noise-limited reliability estimate.

## FIX-H3 — replication rescue with the construction artifact removed

Re-analysing the existing C2 node-drain data:

- **Trough depth:** r1 = 0.0909 (1/11), r3-packed = 0.0909, r3-anti = 0.0455
  (0.5/11); anti-affine **halves the trough depth**, with a significant r×mode
  interaction (p = 0.0065). On the unrounded medians the reduction is **exactly
  50 %**, so it *meets* the pre-declared range-relative bar — but knife-edge: at
  n=8 the anti-affine session depths are bimodal, so the median landing on the
  half-pod is a coincidence and the bar is not robustly cleared.
- **User-route error:** r1 = 0.632, r3-anti = **0.0**; anti-affine **eliminates**
  the user-visible error (rescue 0.632, interaction p ≈ 0).

The original `CONJUNCTION = False` was an artifact of the registered 1-pod depth
margin, which equalled the r=1 range and so demanded a >100 % depth reduction —
impossible. With the artifact removed, the **rescue effect is real and significant
on both faces** (depth halved, user-error eliminated). The depth *bar* is met only
exactly/knife-edge (the r=1 depth is intrinsically ~1 pod), so the robust evidence
is the significant interaction plus the user-error co-primary, not the threshold.

## Honest framing for the thesis

The registered availability tests were construction-limited — un-passable margin
(H3), degenerate face (H4), no-signal regime (H5) — all rooted in pod-delete@r=1
being unable to move availability. A pre-declared, design-corrected follow-up
under node-drain resolves each: **anti-affine replication halves the blast radius
and eliminates user-error; spreading trades a small latency cost for a large
availability gain; and the availability layer is reliable once a fault produces
signal.** This is reported as exploratory (outside the Holm family); it does not
re-open the frozen confirmatory verdicts but shows that two of their "failures"
(H3, H4) were partly construction artifacts whose underlying effects are real.
