# ChaosProbe — C1 + C2 hotelReservation external-validity campaign deposit manifest

**Campaign:** C1 (H1 dose-response, 8 sessions) + C2 (H3 replication-rescue,
24 sessions) on **hotelReservation** — the exploratory external-validity
replication of the online-boutique studies, reported **outside the frozen Holm
family**. Reports: [`C1-HOTEL-REPORT.md`](C1-HOTEL-REPORT.md),
[`C2-HOTEL-REPORT.md`](C2-HOTEL-REPORT.md).

**DOI:** [10.5281/zenodo.20792129](https://doi.org/10.5281/zenodo.20792129)
(version DOI; concept DOI 10.5281/zenodo.20792128). Published 2026-06-22,
record [zenodo.org/record/20792129](https://zenodo.org/record/20792129).

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

- **C1 / H1:** Page's L p(1-sided) = 0.9882 — **no dose-response** of the
  east-west p95 tail (per-level medians 9.34/6.44/6.32/7.10/5.40 ms; trend mildly
  downward). Corroborates OB (which showed a sub-SESOI increase).
- **C2 / H3:** `CONJUNCTION = False`. Significant r×mode interaction on both
  co-primaries; anti-affine r=3 directionally rescues (trough 15 s vs r1 45 s,
  user-error 0.0 vs 0.21) but below the registered margins (depth 0.044 < 0.053,
  error 0.212 < 0.302); packing controls pass. Mirrors OB.

## Deposited (2026-06-22)

Published to Zenodo as a dataset (CC-BY-4.0): the 254 MB
`c1-c2-hotel-deposit.tar.gz` (md5 `e9b75fba4c2d6404c1143f1d37870955`, verified
post-upload), `isSupplementTo` the pre-registration (10.5281/zenodo.20690836) and
`continues` the OB C1/C2 records (10.5281/zenodo.20690737, 10.5281/zenodo.20726729).
DOI [10.5281/zenodo.20792129](https://doi.org/10.5281/zenodo.20792129) (resolves).

To re-verify the archive integrity against the live tree:
`cd chaosprobe && sha256sum -c ../design/c1-c2-hotel-manifest.sha256`
(paths are relative to `chaosprobe/`).
