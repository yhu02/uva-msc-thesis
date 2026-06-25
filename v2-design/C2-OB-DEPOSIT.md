# ChaosProbe v2 — C2 online-boutique campaign deposit manifest

**Campaign:** C2 replication-rescue under node-drain, workload **online-boutique**
(see [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §V2-H3). Between-subjects
design `r ∈ {1, 3}` × `mode ∈ {packed, anti-affine}`, three non-degenerate cells
(**r1-packed, r3-packed, r3-anti-affine**) × 8 replicate sessions = 24
node-drain sessions, nominal condition `f-050`, host-side Locust on the `/`
route. Packed cells use the **round-robin** packed assignment (deviation
**D-2026-06-16-01**).

**DOI:** [10.5281/zenodo.20726729](https://doi.org/10.5281/zenodo.20726729)
(published 2026-06-17; record <https://zenodo.org/record/20726729>). Continues
concept 10.5281/zenodo.20639145; supplements the frozen pre-registration
(10.5281/zenodo.20690836) and references the C1 campaign
(10.5281/zenodo.20690737). The committed SHA-256 manifest
[`c2-roundrobin-manifest.sha256`](c2-roundrobin-manifest.sha256) (all 48 raw
files, computed pre-analysis) + commit `e533d5b` anchor the provenance.

**Provenance.**
- **Data-collection / instrument commit:** `e533d5b` (round-robin packed
  instrument, PRs #293/#294/#295, + the committed campaign driver #296) —
  recorded in every session's `runMetadata.git` with **`dirty = false`**
  (strict-clean tree). All 24 sessions share this commit and pass
  `archive_run.py --strict` (24/24).
- **Analysis-code commit:** same `e533d5b` (`scripts/c2_h3_anova.py`); the driver
  excludes rejected/fully-tainted sessions per the registered rule (none were).
- **Pre-registration reference:** git tag `v2-prereg-freeze`; freeze deposit
  [`FREEZE-DEPOSIT.md`](FREEZE-DEPOSIT.md), DOI
  [10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836).
- **Cluster fingerprint:** k8s v1.28.6, CNI calico, kube-proxy ipvs;
  N = 8 × (2 CPU / 4 GiB) workers.
- **Run parameters (all sessions):** `--v2-levels 0.5`, `--v2-packed-assignment
  round-robin`, `--v2-solver-seed 0`, `--v2-order-seed 1`, `-i 1`, workers
  worker1–8. Driver: `scripts/run_c2_rr_campaign.sh` (committed, #296).

> A first collection recorded `git.dirty = true` (untracked driver script) and
> was **discarded**; this strict-clean re-run supersedes it. The verdict
> replicated (`CONJUNCTION = False`).

## What is archived

The 24 session directories under `chaosprobe/results/c2-roundrobin/` (timestamped
`20260616-*`), each with `summary.json`, the `f-050.json` raw, `charts/`, and an
`artifact-manifest.json` (written by `archive_run.py --strict`: run identity,
git provenance, scenario SHA-256 hashes, K8s/CNI fingerprint, per-file SHA-256).
All 24 passed `archive_run.py --strict`. **NOT in git** (gitignored) — upload to
the DOI archive. Analysis environment is the `chaosprobe/` package at `e533d5b`.

All 24 sessions are **accepted and untainted** (`accepted=true`, 0 pending,
empty `taintReasons`); 8 per cell, none excluded.

## Sessions (collection order → cell → provenance anchor)

All sessions: `solverSeed = 0`, `orderSeed = 1`, condition `f-050`, 1 node-drain
iteration, `git.dirty = false`. The table pins each session's `summary.json`; the
matching `f-050.json` raw checksums are in the committed
[`c2-roundrobin-manifest.sha256`](c2-roundrobin-manifest.sha256) (all 48 files)
and also travel inside the tarball's own `SHA256SUMS`.

| # | session (timestamp) | cell | `summary.json` sha256 |
|---|---|---|---|
| 1 | 20260616-193331 | r1-packed | `e4b3c50f12663a0a6230a3ebbc9c215d207d1cbc4bd7a86e4d05a8d1c504f408` |
| 2 | 20260616-194133 | r1-packed | `0a43095a46f24d727864875eb2eb907f3cad8dcebea57c0346879dc900d10684` |
| 3 | 20260616-194858 | r1-packed | `b42e34faafe813222ffbd4b9cfb5d62d20e897179aea31886ea35e220659734b` |
| 4 | 20260616-195625 | r1-packed | `c71c9c24e4d5c062f6c4fbb3fc0183b16689f17cd5f4bae6dd7b394436935385` |
| 5 | 20260616-200348 | r1-packed | `b15407cc51c96d981594bbf1cb020b2b6f6fe2d539994cd9f29606087a70380e` |
| 6 | 20260616-201105 | r1-packed | `cefef9900dc1a0b77f186863e8df60eeca3d29815f0a5660313db5e9224d9dd3` |
| 7 | 20260616-201833 | r1-packed | `40fa47916c692a505ff5b9edf796278f75f9fc02ce4e1ede5e1f1b29d451a1cc` |
| 8 | 20260616-202606 | r1-packed | `3e029b4411a5bc11d0fd1f79a8c44563357451db1f0aeab1ba9f180894fe8e7f` |
| 9 | 20260616-203312 | r3-packed | `dfba3118d09d375de743c03277f0f1fc95b0cd86a855fc85b5553ced9904e753` |
| 10 | 20260616-204048 | r3-packed | `5d24d28383dfb83e27a6d3f3f4eac66db5d278f81de3919042366e4c6dd55029` |
| 11 | 20260616-204828 | r3-packed | `856f1108d5af3abf51c9753f3410b92294367605ef8d56fe57ab70b1f5c6fc35` |
| 12 | 20260616-205602 | r3-packed | `1ca5f2ceb1b69fe34d68b64c7efc387cb5f4af153362d28d037c6f5ad8cef596` |
| 13 | 20260616-210359 | r3-packed | `804bdc4cd896a5242a0e51936e56d6d4e8c712669accdffaaa4b5c47033e0912` |
| 14 | 20260616-211107 | r3-packed | `08db385984af445c25cf5ff24b67682ec83bec45da168b559eae86beceff1b68` |
| 15 | 20260616-211844 | r3-packed | `3a23fc1c9158a8ec072650a588da4d1bf470ee9d37f06ac47e04aaf9add58e96` |
| 16 | 20260616-212627 | r3-packed | `5b7e13b9e8e893c1d69d43b4bb3b21198a1954a84d3045a32c3d304fe4ec3d1b` |
| 17 | 20260616-213401 | r3-anti-affine | `53e2bb8862aafc4141c1ccf8f7bdf9050d46af13c04e66cf8f2cd15d4336ac5d` |
| 18 | 20260616-214317 | r3-anti-affine | `66f753684446377e1b1a5d7b8a62e6999c23d45be702ec2af4e98a4e82fbd32c` |
| 19 | 20260616-215224 | r3-anti-affine | `e60799703be19eb877e18eae7236265beaf810fd755a051df878c21bf245f549` |
| 20 | 20260616-220144 | r3-anti-affine | `6beb2cb8f64f60e181ecbaf6dfc15148bd2778765bbe6299f570a4a73b2bbced` |
| 21 | 20260616-221049 | r3-anti-affine | `ed6201090e07ba749b8d8270ed260b9d8e3c830b53fe72265645d4912ed55369` |
| 22 | 20260616-221925 | r3-anti-affine | `67b60ff6a135508afdb4e5241f13018d9c662f99bd53a254672ef3c96919dad6` |
| 23 | 20260616-222838 | r3-anti-affine | `deace977664a0b9650bd5e339c6d46481ec6b6a0242039119ba11442668faded` |
| 24 | 20260616-223835 | r3-anti-affine | `1f889f08ddfd370f1f4c4183bf8d34c0758e0a351d8de127d377fcc26b8f57b4` |

Cell assignment follows the deterministic collection order of
`scripts/run_c2_rr_campaign.sh` (8× r1-packed, then 8× r3-packed, then 8×
r3-anti-affine); verified 0 mismatches against each `summary.json`'s own
`v2Session.replicas` / `.mode` / `.packedAssignment`.

## Deposit (published)

Published to Zenodo **2026-06-17** as DOI
[10.5281/zenodo.20726729](https://doi.org/10.5281/zenodo.20726729) (record
<https://zenodo.org/record/20726729>), a standalone dataset deposition linked by
related identifiers to the original thesis-artifact concept
(`continues` 10.5281/zenodo.20639145), the frozen pre-registration
(`isSupplementTo` 10.5281/zenodo.20690836), and the C1 campaign
(`references` 10.5281/zenodo.20690737). Metadata mirrors the C1 deposit
(dataset, CC-BY-4.0, open, creator Hu, Yvo).

Uploaded artifact: `c2-roundrobin-deposit.tar.zst` (46 MB; 333 files incl.
per-session `artifact-manifest.json` + a root `c2-roundrobin/SHA256SUMS`; all 24
sessions passed `archive_run.py --strict`). Verify a download with
`tar --zstd -xOf c2-roundrobin-deposit.tar.zst c2-roundrobin/SHA256SUMS` then
`sha256sum -c` inside the extracted tree; the committed
[`c2-roundrobin-manifest.sha256`](c2-roundrobin-manifest.sha256) pins the same
`summary.json` + `f-050.json` checksums.

Per [`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) §Versioning, this campaign
was deposited **before** its results were written up.
