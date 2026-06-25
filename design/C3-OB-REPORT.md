# ChaosProbe — C3 online-boutique report (H2 + family Holm capstone)

**Campaign:** C3 placement-dependence + DNS intervention on online-boutique;
14 sessions (7 cache-off, 7 cache-on), `r = 1`, placements `f-000` (packed
round-robin) / `f-100` (spread), pod-delete churn, 3 iterations per placement.
Data collected **before** this write-up; all 14 sessions accepted, untainted,
`git.dirty = false`, `archive_run.py --strict` 14/14. This report closes the
primary hypothesis family: with C3 landed, all four members (H1/H2/H3/H5) now
have a primary p-value, so the **Holm correction across the family** is
computed here (§Family capstone).

## H2 — placement-dependence + DNS intervention

Specified as a **two-part, both-must-pass conjunction** over the absolute
during-churn UDP-conntrack drop (`udp_conntrack_drop_entries` = pre-chaos −
during-chaos cluster UDP entries); analysis driver `scripts/c3_h2_dns.py`.

| arm | prediction | result | one-sided p | verdict |
|---|---|---|---|---|
| **(a) placement-dependence** (cache-off) | spread (f=1) drop **>** packed (f=0), paired | **packed > spread in 7/7 pairs** — packed median 11153.4, spread median 8929.1 | 0.98875 (spread>packed) | direction **reversed** → fails |
| **(b) DNS mechanism** (within-spread) | cache-on shrinks spread's drop **≥ 50 %** | **78.0 % median** shrinkage (all 7 pairs 61–85 %) | 0.01125 | **passes** |

**CONJUNCTION = False** (arm (a) fails on direction). Family input
`max(p_a, p_b) = 0.98875`.

**Secondary (not in family):** the packed arm's cache effect was
*not* near-zero as hypothesised — packed's cache-off drop (median 11153.4)
collapses to ≈ 0 with the cache on (median −105.2; one-sided p = 0.01125). The
NodeLocal DNSCache intervention removes the conntrack drop under **both**
placements.

### The placement reversal is genuine, not an artifact

Unlike the H3 round-robin issue (which was a true design artifact corrected
before collection), arm (a)'s reversal is **robust and monotone**: packed
exceeds spread in **every one of the 7 cache-off pairs**, with the two-sided
Wilcoxon at the n = 7 floor (p = 0.0225). Co-locating a service's replicas on a
single node (round-robin packed) **concentrates** the per-node conntrack churn
when pod-delete hits that node, producing a *larger* during-churn UDP drop than
spread placement, which distributes the same replicas (and their churn) across
nodes. The predicted direction — that spread's extra east-west DNS would
dominate — is contradicted by the data. We report it as a directional
finding, **not** retuned post-hoc: placement *does* matter for the conntrack
drop (significantly), but in the opposite direction to the original H2 reading, while
the DNS-cache mechanism (arm b) strongly mitigates it regardless of placement.

## Family capstone — Holm across H1/H2/H3/H5

With C3 complete, the four-member primary hypothesis family is corrected
together by **Holm** at α = 0.05 (`scripts/holm_family.py`, reading each
hypothesis's family-input p verbatim from its own analysis-driver JSON):

| hyp | primary test | family-input p | Holm-adj p | Holm-significant? | bar | **supported?** |
|---|---|---|---|---|---|---|
| **H1** | dose-response (Page's L) | 0.0002 | **0.0008** | yes | effect 13.35 % < 15 % SESOI | **no** (sub-SESOI) |
| **H2** | placement + DNS | 0.98875 | 0.98875 | no | conjunction (placement reversed) | **no** |
| **H3** | replication rescue | 0.0065 | **0.0195** | yes | anti-affine rescue margin unmet | **no** |
| **H5** | layered scorecard ICC | 0.2501 | 0.5002 | no | availability ICC < 0.5 | **no** |

**Family verdict: no primary hypothesis is supported.** Two primaries
(H1's trend, H3's interaction) survive Holm as statistically significant,
but neither hypothesis is *supported*, because Holm significance is necessary
but not sufficient: each member also carries a bar the p-value cannot
speak to, and every member fails its bar or its significance —

- **H1**: the dose-response trend is real and Holm-significant, but the
  effect (13.35 %) sits **below the 15 % SESOI** — a detectable but
  practically-negligible trend, and shaped more like a threshold than a smooth
  dose-response.
- **H2**: placement-dependence replicates with the *opposite* sign, so the
  conjunction fails; its family-input p (0.98875) is far from significant.
- **H3**: the r × mode interaction is significant (Holm-adj 0.0195), but the
  anti-affine **rescue margin** on trough depth is not met — replication does
  not rescue availability to the specified degree.
- **H5**: the layered scorecard's **mechanism** sub-score is highly reliable
  (ICC 0.994), but its **availability** sub-score fails the absolute ICC ≥ 0.5
  bar (0.18), so the required conjunction fails and the family input (0.2501) is
  not significant.

## Synthesis

This study's headline is coherent and honest: **the original placement /
availability / dose-response claims do not replicate under the stricter
design**, while the **underlying conntrack mechanism is robustly real and
individually significant in every campaign** — the DNS-cache intervention
removes 78 % of the spread conntrack drop (and essentially all of packed's), the
scorecard's mechanism layer is near-perfectly reliable (ICC 0.994), and both the
dose-response trend and the replication interaction are statistically
detectable. The conntrack signal ChaosProbe measures is real; the
*placement-quality and availability conclusions* originally drawn from it do not
survive multiplicity-corrected, effect-size-gated, direction-checked testing.

H4 (descriptive) and H6 (exploratory) are reported separately and are not
part of the family.
