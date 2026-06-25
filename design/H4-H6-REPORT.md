# ChaosProbe — H4 placement frontier (descriptive) + H6 (not attempted)

Closes the two non-family deliverables, so **every hypothesis now has a
stated outcome**. H4 is descriptive (a figure + reporting protocol, not a
falsifiable hypothesis); H6 is an exploratory secondary that was not
attempted. Neither is in the primary Holm family (closed in
`C3-OB-REPORT.md`).

## H4 — the placement Pareto frontier (descriptive)

**Protocol (`00-DESIGN.md` §6).** For each designed placement, plot the
**latency face** (pre-chaos
east-west p95 tail — steady-state, placement-determined) against the
**availability face** (during-chaos blast/recovery: trough depth in pods +
user-route error rate), with cluster-bootstrap CIs, and report the
**non-dominated set under margins**. Dominance is declared only with margins,
set from the A/A calibration (`M2-AA-REPORT.md`): **δ_latency = 4.4 ms**, **δ_depth = 1.0 pod**,
**δ_error = 0.302**. A dominates B iff A beats B by ≥ the band on the latency
face **and** on *both* availability DVs (the conservative all-DV reading). All
three DVs are "lower is better". Analysis driver: `scripts/h4_frontier.py`;
figure: [`h4-frontier.png`](h4-frontier.png).

**Frontier set.** The 5 **C1** dose-response cells (f ∈ {0, .25, .5, .75, 1},
r = 1, pod-delete), each with the full latency + availability faces. The 2
**C3** endpoints (f = 0/1, r = 1, cache-on, pod-delete) are overlaid as
**corroboration**, outside the dominance computation.

**C2 (node-drain replication) is excluded — a data-collection finding.** C2 was
run with host-side Locust on the `/` route **only** (no east-west prober), so it
has **no pre-chaos east-west latency face**; its depth is also recorded as a
top-level fraction, not the per-iteration `es_trough_depth_pods` the frontier
uses. C2's replication results therefore live on the availability face alone and
are reported in `C2-OB-REPORT.md` (H3). This is a stated limitation, not a
hidden omission.

### Result

| placement (C1, r=1, pod-delete) | EW p95 pre [ms] (95 % CI) | trough depth [pods] | user err | non-dominated? |
|---|---|---|---|---|
| f = 0 (packed) | **35.74** [33.11, 37.42] | 1.0 | 0.04 | yes |
| f = 0.25 | 38.60 [38.00, 39.14] | 1.0 | 0.04 | yes |
| f = 0.5 | 41.40 [41.09, 42.00] | 1.0 | 0.04 | yes |
| f = 0.75 | 39.55 [36.30, 40.45] | 1.0 | 0.04 | yes |
| f = 1 (spread) | 40.51 [39.62, 41.19] | 1.0 | 0.01 | yes |

C3 corroboration (overlay): f = 0 → 36.2 ms, f = 1 → 39.9 ms; depth 1.0,
error ≤ 0.15 — consistent with the C1 latency face at the endpoints.

**Non-dominated set: all 5 / 5 placements.** No placement margin-dominates any
other.

### Interpretation

The frontier is **degenerate on the availability face**: under pod-delete the
trough depth is ≈ **1 pod for every placement** (pod-delete removes a single pod
by construction) and the user-route error rate is uniformly low (≤ 0.04), so
neither availability DV separates placements by its δ band. The **latency face
does vary** — packed (f = 0, 35.7 ms) is faster than the mid/spread placements
(f = 0.5, 41.4 ms) by 5.7 ms > δ_latency, with non-overlapping CIs (this is the
H1 dose-response signal). But a single-face advantage cannot establish
**margin-dominance** under the all-DV rule, so the protocol correctly
crowns no winner and the entire set is non-dominated.

The two-face latency-vs-availability trade-off the frontier was designed to
reveal is therefore **not realizable from the collected data**: pod-delete (C1/C3)
produces no availability-face variation to trade against latency, and the fault
that *does* move the availability face — node-drain (C2) — has no latency face.
This is consistent with the primary hypothesis family verdict: the placement/
availability conclusions the earlier study drew do not reproduce under the stricter design.
Reported per protocol with the margins stated; no dominance is manufactured from
the latency face alone.

### Limitations

- **Availability-face degeneracy under pod-delete** (depth ≈ 1 pod by the fault's
  construction) — the frontier's availability axis carries no placement signal here.
- **No east-west latency face for node-drain (C2)** — the replication cells that
  would add availability-face spread cannot be placed on the frontier.
- **Single fault class** on the plotted cells (all pod-delete); the latency face
  is pre-chaos hence fault-independent, but the availability face is fault-conditioned.
- Cluster-bootstrap CIs resample over sessions (n = 8 per C1 cell); descriptive,
  not a significance test (none is defined for H4 — frontier cardinality is
  near-self-confirming under noisy CIs, the rationale for the margin rule).

## H6 — iptables-mode direction transfer (NOT ATTEMPTED)

**Status: not attempted.** H6 (exploratory secondary, outside the
primary hypothesis family) defined a sign test that the spread-vs-packed direction
of the UDP-drop contrast is preserved under kube-proxy **iptables** mode (vs the
ipvs mode all campaigns used). It was the **second item in the de-scope
order** (second workload first, then H6). No iptables-mode campaign was run —
the cluster ran kube-proxy **ipvs** throughout, and re-provisioning a parallel
iptables cluster was outside the realized scope. An un-run exploratory arm is
**reported as not-attempted**, which is recorded here. No data, no claim; the
omission neither supports nor falsifies anything (H6 is exploratory and
uncorrected by design).
