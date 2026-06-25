# C3 — implementation scope (H2: placement-dependence + DNS intervention)

**Status:** code **built + converged**; the cluster run is user-gated. The
hypothesis is defined in [`00-DESIGN.md`](00-DESIGN.md) §10 (Arm 1); nothing
here changes the hypothesis, test, SESOI, or n.

**Build status (2026-06-17):**
- ✅ DNS-cache toggle — `chaosprobe/placement/dns_cache.py` (PR #300).
- ✅ `--dns-cache` session knob + wiring + startup self-heal (PRs #301/#302).
- ✅ Analysis driver — `scripts/c3_h2_dns.py` (PR #303), converged over 4
  review rounds (#304/#305/#306 fixed pairing-alignment + rank-consistent
  direction gates on both arms).
- ✅ Campaign driver — `scripts/run_c3_dns_campaign.sh` (this change).
- ⏳ **Cluster steps remain (user-gated):** the go/no-go smoke + cache-state
  verification, then the full campaign run (~3 h/session ⇒ days), then archival.
  The dnsConfig realization is noted below before results are quoted.

## Design (what C3 must produce)

Workload online-boutique, `r = 1`, `pod-delete` churn + host-side Locust. Two
placement extremes × DNS-cache on/off, **paired sessions, randomized cache
order**:

| arm | placement | cache | role |
|---|---|---|---|
| 1 | f = 0 packed | off | primary (a) comparator |
| 2 | f = 1 spread | off | primary (a) + (b) baseline |
| 3 | f = 1 spread | **on** | primary (b) intervention |
| 4 | f = 0 packed | on | secondary (no-cache-effect check, not in family) |

**Outcome:** during-churn **absolute** UDP-conntrack drop (cluster, per-node
phase) — `udp_conntrack_drop_entries`.

**Tests** (§H2):
- **(a) placement-dependence:** paired Wilcoxon signed-rank on per-session
  `(spread − packed)` cache-off UDP drops; one-sided, spread > packed.
- **(b) mechanism:** one-sided Wilcoxon signed-rank of spread's per-session
  paired cache-on-vs-off UDP-drop **shrinkage against the 50 % bar** (D6 form).
- **Conjunction:** (a) AND (b); single input to the outer Holm family is
  `max(p_a, p_b)`. Report each part separately (an (a)-pass/(b)-fail reads as
  "placement-dependent but not via DNS").
- **Secondary (not in family):** packed (f = 0) shows ~no cache
  effect (its UDP pool sits at the noise floor; a material cache effect there is
  evidence against the cross-node-DNS account).
- TCP drop recorded, no prediction.

**n:** M2 power analysis — **n = 7** paired (α = 0.0125, true shrinkage
70–80 %); n = 11 additionally covers the 60 %-shrinkage case
([`M2-AA-REPORT.md`](M2-AA-REPORT.md)). **Confirm the exact n before
running.**

## Reuse (already in the codebase)

- Conntrack protocol prober — `chaosprobe/metrics/conntrack.py` (per-node,
  per-protocol, 5 s, windows in `summary.json`).
- UDP-drop extraction — `scripts/m2_aa_analysis.py`
  (`udp_conntrack_drop_entries`, `udp_cluster_phase_mean`).
- `pod-delete` churn scenario; the placement session f-axis (f ∈ {0,1}, r = 1) — same
  complete-block machinery as C1.
- CoreDNS cache hit/miss metrics — `chaosprobe/metrics/prometheus.py`
  (`coredns_cache_hit_rate_per_sec` / `_miss_rate_per_sec`) to verify cache state.
- Analysis-driver pattern — `scripts/c2_h3_anova.py` (paired/one-sided tests,
  rejected/tainted exclusion, JSON + verdict).

## Build (the new work)

1. **DNS-cache on/off intervention — pod-level dnsConfig** (user-chosen
   mechanism). Deploy the `nodelocaldns` DaemonSet once (node-local cache at the
   link-local IP). Toggle per session by patching app pods'
   `spec.template.spec.dnsConfig.nameservers` to the node-local cache IP
   (cache-on) vs the default cluster DNS (cache-off) — a deployment patch in the
   same class the placement engine already applies, **per-session reversible, no
   kubelet/node reconfig**. Note on the realization: NodeLocal DNSCache is
   realized via pod `dnsConfig` rather than the kubelet `--cluster-dns` default —
   chosen for per-session reversibility under the randomized-order paired design;
   pods still resolve through the node-local cache, so the mechanism (cross-node
   UDP DNS removal) is preserved. The one-time `nodelocaldns` DaemonSet install
   may be user-owned (cluster-level).
2. **Cache-axis session knob** — e.g. `--dns-cache {on,off}` threaded through
   the placement session (mirroring `--packed-assignment`), recorded in
   `session`; plus paired-session + randomized-cache-order bookkeeping (record
   the order seed).
3. **C3 analysis driver** — `scripts/c3_h2_dns.py`, mirroring `c2_h3_anova.py`:
   paired Wilcoxon (a), one-sided shrinkage Wilcoxon vs 50 % (b), secondary
   packed cache-effect, `max(p_a,p_b)` conjunction, rejected/tainted exclusion,
   JSON + printed verdict. 100 % line coverage. UDP-drop extraction reused.
4. **Cache-state verification + go/no-go smoke + campaign driver.** Verify
   cache-on actually removes cross-node UDP DNS (CoreDNS cache metrics + the UDP
   pool drop). Then a **go/no-go smoke**: one spread cache-on-vs-off pair — the
   whole hypothesis hinges on the shrinkage being detectable, so confirm it
   before the full run. Then a `run_c3_*.sh` campaign driver on a strict-clean
   tree (lesson from C2: commit the driver first).

Each build step lands through the PR + converge loop (the C2 discipline).

## Cost / scheduling flag

The M2 power note estimates **~3 h/session** for C3 (vs C2's ~6 min) — the
conntrack-probe protocol is far heavier. At n ≈ 7 paired × the arms, the full
campaign is on the order of **days** of cluster time, not hours. Plan the run
window accordingly; consider whether the per-session conntrack-probe duration
can be tightened without breaking the measurement (analysis, not a
silent change).

## Sequence to "C3 done"

build (1)→(2)→(3) each via PR+converge → cache-state verify → go/no-go smoke →
run the campaign (strict-clean tree) → archive **before** analysis → run
`c3_h2_dns.py` verdict → `max(p)` into the Holm family (with C1/C2) → C3 report
(mirror C1/C2).

## Open items before build starts

- Confirm the exact C3 n (7 vs 11).
- Decide who installs the one-time `nodelocaldns` DaemonSet (likely user-owned,
  cluster-level — like the registry-trust prep).
