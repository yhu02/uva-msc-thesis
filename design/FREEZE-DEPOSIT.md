# ChaosProbe — pre-registration freeze deposit manifest

**Freeze reference:** git tag `prereg-freeze` (the commit that stamped
[`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) FROZEN, 2026-06-13).
Resolve the exact commit with `git rev-parse prereg-freeze`.

**DOI:** [10.5281/zenodo.20690836](https://doi.org/10.5281/zenodo.20690836)
(published 2026-06-14 on Zenodo; "continues" concept
[10.5281/zenodo.20639145](https://doi.org/10.5281/zenodo.20639145)).

This manifest pins, by SHA-256, the exact frozen state archived for the
pre-registration. The committed files are reproducible from the git tag; the
**raw A/A calibration data** (1.8 GB, gitignored) is not in git and must be
uploaded to the DOI archive — its checksums below let anyone verify the
upload matches the frozen reference.

## What is archived

1. **The frozen pre-registration + design docs** (git, under `design/`):
   `00-DESIGN.md`, `01-PREREGISTRATION.md`, `02-WORKPLAN.md`,
   `M1A-REPORT.md`, `M1B-REPORT.md`, `M2-AA-REPORT.md`, `DEVIATIONS.md`,
   this file.
2. **Solver-gate verification artifacts** (git): `m1b-gate-artifact.json`
   (online-boutique), `m1b-gate-artifact-hotel.json` (hotelReservation,
   D7 PASS).
3. **Analysis code** (git, under `chaosprobe/scripts/`):
   `m2_aa_analysis.py` (canonical A/A extraction + registered-unit null
   tests), `aa_block.py` (variance components + noise bands). The whole
   `chaosprobe/` package at the freeze commit is the runnable analysis
   environment.
4. **Raw M2 A/A calibration data** (NOT in git — upload to the DOI archive):
   the 6 session directories under `chaosprobe/results/aa/` (3
   identical-placement pairs), each with `summary.json` + 5 per-condition
   `f-*.json` raws. 1.8 GB total.

## SHA-256 — committed files (frozen state)

```
9e02672737ac6fff1a8dd95fa7ee8730caadc08ba060156e7e5a623ea7934e46  design/00-DESIGN.md
5d9da20181d083c0a7864af3de44c7d0f126e8570df4111909da31e1879e33d8  design/01-PREREGISTRATION.md
11390f23da6bfa7ef9eb289abb238e6668c77b063c5eeab4424b48d4f3d94c8e  design/02-WORKPLAN.md
46f9e3d940929799e009a40b81dfc65a9d83b65800b80d38a82970e0e4b0ee41  design/M1A-REPORT.md
a98fdd053c233fe1ef986839b79ed7d68844a0f2c84dad4447f0f93f7c3a2d98  design/M1B-REPORT.md
7d137c4d7ed8bae6040653da8065ea1449e4e4385f05ff708e1787f4e3cf254b  design/M2-AA-REPORT.md
4bac1ec4c6f9f9cfe2d6e04c9cb62dfd97e99ef3552f519e98efce851e51289b  design/DEVIATIONS.md
380f749df195901820d56507a8e1832e31516e1e121a314f7e15b83cd06b32cb  design/m1b-gate-artifact.json
b13523ce0dca251f9374b899542eb1cd61e7070c70d1fbb5b5f5638d3b58e3ff  design/m1b-gate-artifact-hotel.json
53ebfcd90ffc651d128cbdcea3fdfbd9ca6629820a9612214747c57bdc0597da  chaosprobe/scripts/m2_aa_analysis.py
184fc20bcec5e98594401f6b7cc4bc2ab6d652a2934986f3856c0e243b7c28e6  chaosprobe/scripts/aa_block.py
```

(`01-PREREGISTRATION.md`'s hash is its content at the moment of staging the
freeze commit; this manifest does not checksum itself.)

## SHA-256 — raw A/A session summaries (Zenodo upload)

```
0f183820802bfbab28ae41e9b2700e5f5dacce28ab81c80cd23f3968fdd8f318  aa/20260611-184530/summary.json
4e824450e0e270c034d2def2cd78f06dccf838b739f37f19a7d78b7cbffc32ad  aa/20260611-213923/summary.json
cfd02c1e5344b9ddd00013ca5aa240f1567372b4b94336bf318972d3782e56d2  aa/20260612-002516/summary.json
e8851d6aabb02178ee3fdaf3674e5b42c05c21b98d6cf85bdd7a16f579fbd6eb  aa/20260612-030816/summary.json
c234df92d322499accb3b45078fb885851c5d79a96889ab9d9b4edf68ef347df  aa/20260612-074544/summary.json
d682101be7b26536aa3958731e541c88ffa0ddc12228b9b05cf7f2fcb95e5123  aa/20260612-103215/summary.json
```

(Per-condition `f-*.json` raws — 30 files — are bundled alongside each
`summary.json`; their checksums travel inside the deposit tarball's own
`SHA256SUMS` file, generated when the bundle is built.)

## Depositing (user-owned — outward-facing, mints the DOI)

The committed state is pushed and tagged. The raw-data tarball is staged at:

```
/tmp/prereg-freeze-deposit.tar.zst   (built at freeze time; see below)
```

To publish:

1. Create a new Zenodo deposit (or a new version under the earlier study's
   **concept** DOI 10.5281/zenodo.20639145 — the version-independent concept,
   not the earlier study's version record 10.5281/zenodo.20639146 — if you want them linked).
2. Upload: the raw-data tarball **and** a clone/export of the repo at the
   `prereg-freeze` tag (or just link the GitHub release of that tag —
   Zenodo–GitHub integration archives a tagged release automatically).
3. Title: “ChaosProbe — pre-registration (frozen) + M2 A/A calibration
   data”. Set the publication date to the freeze date (2026-06-13).
4. Publish to mint the DOI, then record it in the **DOI** line above and in
   the prereg header, and commit that one-line update.

Until the DOI is minted, the **git tag `prereg-freeze` is the immutable
freeze reference**; the DOI adds third-party, citable permanence.
