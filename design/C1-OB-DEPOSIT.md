# ChaosProbe — C1 online-boutique campaign deposit manifest

**Campaign:** C1 dose-response, workload **online-boutique** (the first of the
two C1 workloads; see [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Session
design and §Workloads). Complete-block design: every session visits all five
cross-node-fraction levels `f ∈ {0, 0.25, 0.5, 0.75, 1.0}` in randomized order
from a recorded seed, `r = 1`, churn (`pod-delete` on productcatalogservice) +
host-side Locust.

**DOI:** [10.5281/zenodo.20690737](https://doi.org/10.5281/zenodo.20690737)
(published 2026-06-14 on Zenodo; "continues" concept
[10.5281/zenodo.20639145](https://doi.org/10.5281/zenodo.20639145)).

**Provenance.**
- **Data-collection commit:** `2ec934f` (the M3-prep tip; recorded in every
  session's `runMetadata.git.commit`, `dirty: false`). All 8 sessions share
  this commit.
- **Analysis-code commit:** the analysis is run on **current `main` at or after
  `7d6943e`** (PR #282), which adds the frozen D3 UDP-slope taint gate (a
  post-collection analysis step, so the data commit predates it by design;
  deviation **D-2026-06-14-01**, derived blind to this campaign). The D3
  slope-taint is **OFF** for the H1 / H5 analyses of this campaign per
  deviation **D-2026-06-14-02** (it did not generalize from the A/A block to
  C1's per-level re-placement regime; see [`DEVIATIONS.md`](DEVIATIONS.md)).
- **Pre-registration reference:** git tag `prereg-freeze`
  (commit `20097c1`); the freeze deposit is [`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md).
- **Cluster fingerprint** (per session `runMetadata`): k8s v1.28.6,
  containerd 1.7.11, CNI calico, kube-proxy ipvs; N = 8 × 4 GiB workers.

## What is archived

The 8 session directories under `chaosprobe/results/c1-online-boutique/`
(`s01`–`s08`), each with `summary.json`, the 5 per-condition `f-*.json` raws,
`charts/`, and an `artifact-manifest.json` (written by
`chaosprobe/scripts/archive_run.py --strict`: run identity, git provenance,
scenario SHA-256 hashes, K8s/CNI/kube-proxy fingerprint, and a SHA-256 of every
file in the session). All 8 passed `archive_run.py --strict` (clean tree,
scenario hashes present, runMetadata present). 2.4 GB total; **NOT in git**
(gitignored) — upload to the DOI archive. The analysis environment is the
`chaosprobe/` package at the analysis-code commit above.

## Sessions (orderSeed → provenance anchors)

All sessions: `solverSeed = 0`, `r = 1`, 5 f-levels × 3 iterations (120 churn
iterations total). Per-file checksums travel inside the tarball's own
`SHA256SUMS` (177 files); the anchors below pin each session's `summary.json`
and `artifact-manifest.json`.

| session | orderSeed | `summary.json` sha256 | `artifact-manifest.json` sha256 |
|---|---|---|---|
| s01 | 1 | `b2282ccedc64587bede3ab3cfad44bfc0a5be59690deb3ff29a4bb54f2301456` | `e43ec91c84e61a78fec4c2fbeb6ac13f6aed09dd8a5f7d6a8acf007bd27a3750` |
| s02 | 2 | `3e21b50f262997c2a2d2dc410ad6586886c89876eed5f4a21f0da9487cb9cf22` | `1c3a3ca8580d382987649a86a022f834fc2ee9dd38f010a410545176008fb2b1` |
| s03 | 3 | `666f4f38a620d7cba77faadac1fa932f3f92489e6cdb99e30d0b8b39f0d2860f` | `d19535978adf62d1aee220b2ba765da786740d0f4c4e862c6b307406ecc4e9d1` |
| s04 | 4 | `293fe0172fe2b2fbf5c905b5d563334eee21a3dbbae86746db1e452a3fb3dac1` | `266f95694e1c34b8f4a65ee92dcb9b98814f89a607998eb07e3719e6e75612e3` |
| s05 | 5 | `ff5c0ca1bce4e1150110387d4aaed017890ed8c3051d063b74e5dbddd142fe6b` | `594b898ebd759023c3b8b02d4a155d1e998b4fd9e6fbca4d681dd0f269b6de2e` |
| s06 | 6 | `b906c80f5d22873aebc1d41a9c2afe9d6bd58a1dbee415b6ed639acc9b558149` | `8070a10330641c2490eae2e65d5bcb16083e11de7330899c726c334152408969` |
| s07 | 7 | `47b36a21c1b2be232f7b9c3032eac85124066ebee9d4ace2fcd1739ca398be46` | `1ae1ec90efab1193e516e8173d8b67f49f80668e67b4f85a7f1bd81a3f2a4910` |
| s08 | 8 | `c1852f42a728e98b17dea4624fc1dc9f63c62e10597771afcf0baccfb662ce5f` | `49ffc9572771da066e7a2f2956f38ed1a2009d254363261839dd5a513194b8ab` |

## Depositing (user-owned — outward-facing, mints the DOI)

The deposit tarball is staged at:

```
/tmp/c1-online-boutique-deposit.tar.zst   (153 MB; 2.4 GB uncompressed; 177 files + a root SHA256SUMS)
```

To publish:

1. Create a new Zenodo deposit — recommended as a **new version under the same
   concept DOI** as the freeze deposit, so the pre-registration and its first
   campaign are linked.
2. Upload the tarball. (Its internal `SHA256SUMS` lets anyone verify the upload
   against the anchors above; verify with `tar --zstd -xOf … c1-online-boutique/SHA256SUMS`
   then `sha256sum -c` inside the extracted tree.)
3. Title: "ChaosProbe — C1 dose-response campaign (online-boutique)". Set the
   publication date to the collection date (2026-06-13/14).
4. Publish to mint the DOI, then record it in the **DOI** line above and commit
   that one-line update.

Per [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Versioning, this campaign
is deposited **before** its results are analyzed for writing. Until the DOI is
minted, the data-collection commit `2ec934f` plus these checksums are the
provenance reference.
