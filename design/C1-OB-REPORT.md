# C1 report — dose-response campaign, **online-boutique** (preliminary)

Confirmatory results for the first C1 workload (online-boutique). **Preliminary**
in one strict sense: the registered confirmatory family (H1, H2, H3,
H5) is corrected together by **Holm across all campaigns**, and H2/H3
are tested by C2/C3 — so the per-hypothesis p-values below are *uncorrected*
and final significance waits on the full family. C1 tests **H1** and
**H5**; H2/H3 do not apply to pod-delete dose-response.

**Provenance.** Data: 8 complete-block sessions, online-boutique, collected at
commit `2ec934f` (clean), DOI
[10.5281/zenodo.20690737](https://doi.org/10.5281/zenodo.20690737)
([`C1-OB-DEPOSIT.md`](C1-OB-DEPOSIT.md)). Pre-registration frozen at tag
`prereg-freeze`, DOI
[10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836)
([`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md)). Analysis at commit `9dbfdb3+`
(`scripts/c1_h1_trend.py`, `scripts/scorecard.py`). Deposited **before** analysis
per [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Versioning.

## Campaign as run

Complete-block design: every session visits all five cross-node-fraction levels
`f ∈ {0, 0.25, 0.5, 0.75, 1.0}` in randomized order (recorded seed), `r = 1`,
churn (`pod-delete` on productcatalogservice) + host-side Locust. 8 sessions
(order-seeds 1–8, solver-seed 0), 5 levels × 3 iterations = 120 churn
iterations. All 8 sessions `doctor --strict` clean at collection; all f-targets
hit exactly.

## D3 taint disposition (deviation D-2026-06-14-02)

The frozen D3 pre-window UDP-slope taint (D-2026-06-14-01) is **OFF** for the
H1 / H5 analyses here. Applied to C1 it taints **24/24 iterations at both
f-025 and f-050** (zero complete blocks → both tests unrunnable). Diagnosis
([`DEVIATIONS.md`](DEVIATIONS.md) D-2026-06-14-02):

- **Not an instrument artifact** — pre-chaos UDP sampling is identical between
  the A/A block and C1 (≈88–96 samples, ~60 s window, 8 nodes).
- **A structural regime difference.** A/A's interior levels had a small,
  *growing* pre-window UDP pool (band positive); C1 re-places per f-level, so
  its pre-window catches a large post-re-placement DNS/UDP conntrack burst
  *draining* at every non-zero level. Per-level A/A band vs C1 slope (entries/min):

  | level | A/A D3 band | C1 slope (median) | C1 out-of-band |
  |---|---|---|---|
  | f-000 | [−81, 56] | −15 | 1/24 |
  | f-025 | [−358, +1022] | −9856 | **24/24** |
  | f-050 | [+414, +1084] | −9161 | **24/24** |
  | f-075 | [−11211, −3867] | −8057 | 0/24 |
  | f-100 | [−8766, −5519] | −6276 | 5/24 |

- **The taint discards the cleanest data.** The H1 latency baseline
  (`ew_p95_pre_ms`) at the tainted levels is the *most* stable of all levels
  (f-050 CV ≈ 2 %, f-025 ≈ 12 %; the *passing* f-075 is the noisiest at ≈ 18 %).
  East-west p95 is TCP/gRPC latency; the pre-window UDP pool is DNS conntrack and
  is not a validity precondition for it.

Results are reported **both ways**; the slope-taint stays in force where the UDP
pool *is* the measurement (H2, C3). Forward fix recorded for C2/C3: lengthen
the post-(re)placement settle so the pre-window starts after the burst drains.

## H1 — dose-response of the east-west tail

Registered primary test: **Page's L** trend test (tie-corrected) over the five
ordered f-levels, session-condition medians of `ew_p95_pre_ms` as the unit
(median over inter-service routes, loadgen→ excluded, pre-chaos window — the D4
operationalization), monotone-increase alternative.

| | primary (D3-off) | sensitivity (D3-on) |
|---|---|---|
| complete blocks | 8 | 0 (unrunnable) |
| Page's L | L = 410, z = 3.54 | — |
| p (one-sided, uncorrected) | **0.0002** | — |
| per-level median (ms) | 35.74 / 38.60 / 41.40 / 39.55 / 40.51 | — |
| f0→f1 effect | **+13.35 %** | — |
| 15 % SESOI | not met | — |

**Outcome: a monotone increasing trend is detected (p = 0.0002), but the total
effect (+13.35 %) is below the registered 15 % SESOI.** Per
[`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §H1, a statistically
detectable but sub-SESOI trend is reported as **below the SESOI, not as
support**. The medians rise then plateau/dip slightly at f-075 (39.55) — not the
clean two-regime step that would trigger the distinct "threshold, not
dose-response" registered outcome, but not a strict monotone climb either; Page's
L (which rewards the overall ordering) is significant regardless. The
Spearman-over-levels sensitivity check is non-confirmatory and not computed here.

## H5 — layered scorecard reliability

Registered evaluation: per-sub-score condition-level ICC vs the aggregate
(`ICC_old`); required conjunction of **availability** ∧ **mechanism**; user-tail
exploratory. Sub-score formulas per [`DEVIATIONS.md`](DEVIATIONS.md)
D-2026-06-13-01; absolute reliability bar ICC ≥ 0.5.

Aggregate comparator: **ICC_old = 0.066** [0.027, 0.343].

| sub-score | role | ICC (primary, D3-off) | 95 % CI | ≥ 0.5? | verdict |
|---|---|---|---|---|---|
| availability | required | **0.180** | [0.042, 0.468] | no | **FAIL** |
| mechanism | required | **0.994** | [0.635, 0.999] | yes | **PASS** |
| user-tail | exploratory | 0.741 | [0.040, 0.886] | — | excluded |

**Required conjunction: FAIL** (availability fails the bar). This is a
**sensible registered falsification, not a method failure**: pod-delete churn
produces no sustained endpoint outage, so the availability layer has little
reproducible between-condition signal to estimate — that face is expected to
bite under **node-drain (C2)**. The *mechanism* layer is the headline: ICC 0.994
vs ICC_old 0.066 — on this campaign the layered scorecard's conntrack-
reconvergence sub-score is far more test-retest reliable than the aggregate
score. (Caveat: a 0.994 point estimate on a single campaign with a wide-ish
lower CI bound is strong but not to be over-read; reliability is re-evaluated
across workloads/environments later.)

**Sensitivity (D3-on):** with the slope-taint applied, only 3 conditions survive
(f-025/f-050 fully tainted); the conjunction still **FAILs** (mechanism ICC 0.946
but CI/​p no longer clear the bar at 3 conditions/24 obs — degraded and
underpowered). **The FAIL verdict is therefore robust to the D3 choice**, which
strengthens the conclusion rather than resting it on the deviation.

## Limitations

- **Preliminary pending Holm** across the confirmatory family (H1/H2/H3/H5);
  C1 supplies only H1 + H5, and the family is corrected once C2/C3 land.
- **Single workload.** hotelReservation C1 is deferred (no pre-registered churn
  fault yet); external validity across workloads is unestablished.
- **Availability face untested by C1.** Its reliability/effect awaits node-drain
  (C2).
- **D3 slope-taint withdrawal** is a non-blind deviation (decided after seeing it
  taint the interior levels), mitigated by the objective diagnosis above, the M2
  F2 pre-flag, and the both-ways reporting; a reader may weigh it differently.
