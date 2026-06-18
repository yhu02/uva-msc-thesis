# ChaosProbe v2 — C3 online-boutique deposit manifest

**Campaign:** C3 placement-dependence + DNS intervention, workload
**online-boutique** (see [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md)
§V2-H2). Within-subjects placement contrast `f ∈ {0, 1}` (packed round-robin /
spread) at `r = 1`, crossed with a between-subjects DNS-cache axis
`dnsCache ∈ {off, on}` (NodeLocal DNSCache via pod `dnsConfig`, deviation
**D-2026-06-17-01**); 7 cache-off + 7 cache-on sessions = **14 sessions**, each
visiting both placements under pod-delete churn, 3 iterations per placement,
host-side Locust on the `/` route.

**DOI:** [10.5281/zenodo.20748970](https://doi.org/10.5281/zenodo.20748970)
(published 2026-06-18; record <https://zenodo.org/record/20748970>). Continues
concept 10.5281/zenodo.20639145; supplements the frozen pre-registration
(`isSupplementTo` 10.5281/zenodo.20690836) and references the C1
(10.5281/zenodo.20690737) and C2 (10.5281/zenodo.20726729) campaigns. The
committed SHA-256 manifest [`c3-dns-manifest.sha256`](c3-dns-manifest.sha256)
(all 42 raw files — 14 × `summary.json` + `f-000.json` + `f-100.json`, computed
pre-analysis) + commit `2409f35` anchor the provenance.

**Provenance.**
- **Data-collection / instrument commit:** `2409f35` (round-robin packed
  instrument + the `--v2-dns-cache` knob, PRs #300/#303–#308) — recorded in
  every session's `runMetadata.git` with **`dirty = false`** (strict-clean
  tree). All 14 sessions share this commit and pass `archive_run.py --strict`
  (14/14).
- **Analysis-code commit:** `scripts/c3_h2_dns.py` (V2-H2 verdict) and
  `scripts/holm_family.py` (the four-member family Holm capstone), at the report
  commit.
- **Pre-registration reference:** git tag `v2-prereg-freeze`; freeze deposit
  [`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md), DOI
  [10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836).
- **Cluster fingerprint:** k8s v1.28.6, CNI calico, kube-proxy ipvs; NodeLocal
  DNSCache deployed (kubelet `clusterDNS = 169.254.25.10`); N = 8 × (2 CPU /
  4 GiB) workers.
- **Run parameters (all sessions):** `--v2-levels 0,1`, `--v2-replicas 1`,
  `--v2-mode packed`, `--v2-packed-assignment round-robin`, `--v2-dns-cache
  {off,on}`, `--v2-solver-seed 0`, `--v2-order-seed 1`, `-i 3`, workers
  worker1–8. Driver: `scripts/run_c3_dns_campaign.sh` (committed, #307), 7
  matched cache-off/cache-on pairs, within-pair cache order seeded-randomized
  (seed 20260617).

## What is archived

The 14 session directories under `chaosprobe/results/c3-dns/` (timestamped
`20260617-*` / `20260618-*`), each with `summary.json`, the `f-000.json` /
`f-100.json` raws, `charts/`, and an `artifact-manifest.json` (written by
`archive_run.py --strict`: run identity, git provenance, scenario SHA-256
hashes, K8s/CNI fingerprint, per-file SHA-256). All 14 passed `archive_run.py
--strict`. **NOT in git** (gitignored) — uploaded to the DOI archive. Analysis
environment is the `chaosprobe/` package at the report commit.

All 14 sessions are **accepted and untainted** (`accepted = true`, 0 pending,
empty `taintReasons` on every iteration); 7 per cache mode, none excluded.

## Sessions (collection order → cache mode → provenance anchor)

All sessions: `solverSeed = 0`, `orderSeed = 1`, `replicas = 1`, conditions
`f-000` (packed) + `f-100` (spread), 3 churn iterations each, `git.dirty =
false`. The table pins each session's `summary.json`; the matching
`f-000.json` / `f-100.json` raw checksums are in the committed
[`c3-dns-manifest.sha256`](c3-dns-manifest.sha256) (all 42 files) and also
travel inside the tarball's own `c3-dns/SHA256SUMS`.

| # | session (timestamp) | cache | `summary.json` sha256 |
|---|---|---|---|
| 1 | 20260617-190308 | off | `83169d7909025a4c9ec04b669f433ef6bb76ab3a25efb134dfde2f18f7fc6a82` |
| 2 | 20260617-200936 | on | `9215b06924cb1beddfedaced72ae41e56a3452adc9da064a89842b2d0d3a2eae` |
| 3 | 20260617-211941 | off | `3a91676fe1ac9b049943b3eb949caf030f13cc14b1c3e3dc22070107ec4daf17` |
| 4 | 20260617-222640 | on | `42c3405dad51406ceaee287a353d963cf5756b77db1db538fd6f6a85569d74e0` |
| 5 | 20260617-233219 | off | `be5159c33ef8416ffa2659f7df4dc25a26e5236d59e013c267731ea58c23fcc9` |
| 6 | 20260618-003748 | on | `b344a48d49ed315293a9dff97b985c1e04ae14163d1334e4efe82494bb952ea5` |
| 7 | 20260618-014314 | off | `088bc01c797bf1d2b0e3d481e4d3c07954e077044cb43e4cb9f411032323c953` |
| 8 | 20260618-024813 | on | `8ad7819b8712a82ef4c7d0b92f30638a4cae85c14ad4004048ccfbc964223214` |
| 9 | 20260618-035424 | on | `8c08de738636707332a7d2586b0a743670bb6526f6c55ad9649ef6655392bbd2` |
| 10 | 20260618-045920 | off | `1ca399f0515838aaa0e0ce637b43f88ea1f118b041a945aa6d0fb2280fc0645b` |
| 11 | 20260618-061216 | off | `01322da953e2491b264d7f1af9a2845a256b5e6071484fd3e61642fdadb414ae` |
| 12 | 20260618-072128 | on | `e01864d14c3eb06d3466c991748d8830be4376d1279ba6780b65fc1926b7d849` |
| 13 | 20260618-082629 | off | `f7e229d1c0814fdc027c42a00dc10026c5143cf0a0ca63d6f05b0fa2a30ed64b` |
| 14 | 20260618-093242 | on | `97beaf73fa45ade69e18bfa1551abdf1fdc30b152b4351d42f4f7413df8f840c` |

Cache assignment follows the deterministic collection order of
`scripts/run_c3_dns_campaign.sh` (7 cache-off/cache-on pairs; within each pair
the cache order is seeded-randomized, so the off/on sequence above is not
strictly alternating — pairs 4–5 and 7 show the randomized order). Verified 0
mismatches against each `summary.json`'s own `v2Session.dnsCache`.

## Deposit (published)

Published to Zenodo **2026-06-18** as DOI
[10.5281/zenodo.20748970](https://doi.org/10.5281/zenodo.20748970) (record
<https://zenodo.org/record/20748970>), a standalone dataset deposition linked by
related identifiers to the original thesis-artifact concept
(`continues` 10.5281/zenodo.20639145), the frozen pre-registration
(`isSupplementTo` 10.5281/zenodo.20690836), and the C1
(`references` 10.5281/zenodo.20690737) and C2
(`references` 10.5281/zenodo.20726729) campaigns. Metadata mirrors the C1/C2
deposits (dataset, CC-BY-4.0, open, creator Hu, Yvo).

Uploaded artifact: `c3-dns-deposit.tar.zst` (119 MB; 267 files incl. per-session
`artifact-manifest.json` + a root `c3-dns/SHA256SUMS`; all 14 sessions passed
`archive_run.py --strict`). Tarball sha256
`1d3f850939446f04b1c0bc6eccc3b60040b4b78cadfea5e013b8945ece5cae7a`. Verify a
download with `tar --zstd -xOf c3-dns-deposit.tar.zst c3-dns/SHA256SUMS` then
`sha256sum -c` inside the extracted tree; the committed
[`c3-dns-manifest.sha256`](c3-dns-manifest.sha256) pins the same `summary.json`
+ `f-000.json` / `f-100.json` checksums.

Per [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Versioning, this campaign
was deposited **before** its results were written up.
