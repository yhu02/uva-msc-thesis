# Design-corrected re-analysis — declared scope (exploratory, outside the Holm family)

**Status: declared BEFORE the corrected analyses are run / the new data are
looked at** (criteria fixed before analysis, applied to a design fix).
This arm is **exploratory** — the primary Holm family is closed;
nothing here re-opens or re-scores it. It is reported as a *design-corrected
follow-up*: the original primary tests for the availability axis were
construction-limited, and these corrected designs resolve that limit.

## The fault being corrected

Three primary tests touch an **availability axis that could not vary** in
the regime they were computed in:

1. **H3 trough-depth co-primary** — the margin (1.0 pod = 0.0909
   fraction) *equals the realized r=1 trough depth* (0.0909), so the depth rescue
   can never clear it regardless of data: the co-primary is un-passable by
   construction.
2. **H4 placement frontier** — its availability face was computed on C1
   (`pod-delete` at r=1), under which trough depth is ≈ 1 pod for *every*
   placement, so the availability axis is degenerate and the frontier is
   trivially all-non-dominated.
3. **H5 availability sub-score** — its ICC (0.180) was computed on the same
   `pod-delete` C1 data, where there is no sustained outage, so the low ICC
   reflects absence of signal rather than unreliability.

**Root cause:** `pod-delete` at r=1 cannot move availability; the availability
measurements need a fault that produces a real, placement-dependent outage —
`node-drain`.

## Corrected designs (criteria fixed here, before analysis)

### FIX-H3 — replication rescue with a range-relative depth margin (re-analysis)
Re-analyze the **existing** C2 node-drain data (it already has the availability
signal; only the *criterion* was faulty). Replace the absolute 1.0-pod depth
margin with a **relative-reduction** bar: the depth rescue passes iff the
anti-affine trough depth is **≤ 50 % of the realized r=1 trough depth** (i.e. a
≥ 50 % relative reduction). The packing control (r3-packed ≈ r1) and the
user-error co-primary (margin 0.302) are unchanged. Also report the
**integrated-outage** metric (trough depth × duration) proposed in the thesis's
own future-work. Declared support: significant r×mode interaction AND the
≥ 50 % depth reduction AND the user-error rescue ≥ 0.302 AND both packing controls.

### FIX-H4 — placement frontier with a live availability face (new run)
New **node-drain dose-response** campaign: `node-drain` × the five cross-node
fractions f ∈ {0, 0.25, 0.5, 0.75, 1.0}, r=1, 8 complete-block sessions × 3
iterations (the C1 design, fault swapped to node-drain). Two faces:
- **availability** = during-drain EndpointSlice trough depth (fraction of app
  ready endpoints lost), which now varies with placement (blast radius = services
  co-located with the drain target);
- **latency** = pre-chaos east-west p95.
Declared question: is the availability axis **non-degenerate** (varies
materially across placements), and does the frontier show a latency×availability
**trade-off** or a **dominance** ordering? (Either is a real result; degeneracy
is the failure mode being corrected.)

### FIX-H5 — availability sub-score reliability under outage (new run)
Compute the layered-scorecard **availability** sub-score condition-level
test-retest ICC on the new node-drain dose-response sessions (the regime now
produces an outage). Bar ICC ≥ 0.5 (unchanged). Mechanism and user-tail
sub-scores reported alongside.

## Honesty / framing

This does **not** erase the original limitation — it discloses it and resolves
it. The thesis will report: the original availability tests were
construction-limited (un-passable margin / degenerate face / no-signal regime);
a design-corrected, declared follow-up under `node-drain` resolves each and
finds [result]. The corrected arm is exploratory (no Holm membership, no
multiplicity owed), provenance-gated to the same bar as the
primary campaigns.
