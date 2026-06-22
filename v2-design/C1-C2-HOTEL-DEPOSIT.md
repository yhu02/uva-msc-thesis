# ChaosProbe — C1 + C2 hotelReservation external-validity campaign deposit manifest

**Campaign:** C1 (V2-H1 dose-response, 8 sessions) + C2 (V2-H3 replication-rescue,
24 sessions) on **hotelReservation** — the exploratory external-validity
replication of the online-boutique studies, reported **outside the frozen Holm
family**. Reports: [`C1-HOTEL-REPORT.md`](C1-HOTEL-REPORT.md),
[`C2-HOTEL-REPORT.md`](C2-HOTEL-REPORT.md).

**DOI:** _pending — minted at deposit (user-owned)._

**Provenance.**
- Code: `main` commit `bdf1ccb` (all readiness/topology fixes merged: #317–#324).
  Every one of the 32 sessions records `runMetadata.git.dirty = false`.
- Quality: all 32 sessions `doctor --strict` clean (0 errors). 1 of 144 churn
  iterations carried an `app_ready_timeout` taint (session order-seed 8,
  post-pause restart) and is excluded by the registered healthy-only rule.
- Manifest frozen **before** analysis (deposit-before-analysis):
  `c1-c2-hotel-manifest.sha256` (130 raw JSON files; verified byte-identical to
  the pre-verdict freeze).

## What is archived

- `results/c1-hotel/` — 8 C1 complete-block sessions (5 levels × 3 iterations
  each), `pod-delete` on `search`, r=1.
- `results/c2-hotel/` — 24 C2 sessions (3 cells × 8: r1-packed / r3-packed /
  r3-anti-affine), `node-drain`, f=0.5, round-robin assignment.
- `c1-c2-hotel-manifest.sha256` — sha256 of every raw file (the integrity anchor).
- Staged tarball: `/tmp/c1-c2-hotel-deposit.tar.gz` (≈254 MB).

## Verdicts (computed after the freeze)

- **C1 / V2-H1:** Page's L p(1-sided) = 0.9882 — **no dose-response** of the
  east-west p95 tail (per-level medians 9.34/6.44/6.32/7.10/5.40 ms; trend mildly
  downward). Corroborates OB (which showed a sub-SESOI increase).
- **C2 / V2-H3:** `CONJUNCTION = False`. Significant r×mode interaction on both
  co-primaries; anti-affine r=3 directionally rescues (trough 15 s vs r1 45 s,
  user-error 0.0 vs 0.21) but below the registered margins (depth 0.044 < 0.053,
  error 0.212 < 0.302); packing controls pass. Mirrors OB.

## Depositing (user-owned — outward-facing, mints the DOI)

1. Verify the manifest against the live files:
   `cd chaosprobe && sha256sum -c ../v2-design/c1-c2-hotel-manifest.sha256`
   (paths are relative to `chaosprobe/`).
2. Create a Zenodo record. Title: _"ChaosProbe — C1+C2 hotelReservation
   external-validity campaign"_. Mark as a dataset; link `isSupplementTo` the
   pre-registration (zenodo.20690836) and `isContinuationOf` the OB C1/C2 records.
3. Upload `/tmp/c1-c2-hotel-deposit.tar.gz`, publish, then record the minted DOI
   back into this file and the two reports (the `_pending_` placeholders).

> The upload is token-gated and outward-facing, so it is yours to run — pasting a
> Zenodo token into the agent prompt is the recurring plaintext-secret hazard;
> better to run the upload yourself. I have everything else staged and verified.
