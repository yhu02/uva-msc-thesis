# ChaosProbe v2 — pre-registration freeze deposit manifest

**Freeze reference:** git tag `v2-prereg-freeze` (the commit that stamped
[`01-PREREGISTRATION.md`](01-PREREGISTRATION.md) FROZEN, 2026-06-13).
Resolve the exact commit with `git rev-parse v2-prereg-freeze`.

**DOI:** _[pending — minted on Zenodo deposit; see “Depositing” below. Update
this line and the prereg header with the DOI once published.]_

This manifest pins, by SHA-256, the exact frozen state archived for the
pre-registration. The committed files are reproducible from the git tag; the
**raw A/A calibration data** (1.8 GB, gitignored) is not in git and must be
uploaded to the DOI archive — its checksums below let anyone verify the
upload matches the frozen reference.

## What is archived

1. **The frozen pre-registration + design docs** (git, under `v2-design/`):
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
   the 6 session directories under `chaosprobe/results/v2-aa/` (3
   identical-placement pairs), each with `summary.json` + 5 per-condition
   `f-*.json` raws. 1.8 GB total.

## SHA-256 — committed files (frozen state)

```
e8f98d95fd0a890895210e0ea3f108943bd3e67d375f384e96694ed8cd875587  v2-design/00-DESIGN.md
b0dc252515765cf81435e11d31b45b36411a095f0d14e26e208b675e250c14d4  v2-design/01-PREREGISTRATION.md
a3056a1f654ce4e00eaaff55a8a7817a80f1bc1b035b2daf91fb0960ea3cae44  v2-design/02-WORKPLAN.md
a14b8a414d1536d2112693a8e28cd343c0cca9df13de21727fe4c4ca23f4fe8f  v2-design/M1A-REPORT.md
6ec886b906e972c5648fec0128b19b38605aad63ac8c288e3d99e5fad9104a9b  v2-design/M1B-REPORT.md
9b6676e3e00ff83e1bed3b1729addf17f4f863a7b407abef65a96df86dc40bd5  v2-design/M2-AA-REPORT.md
dd78e39b0241247eb08c67e29cf10096da2df8eb8bc2c9237787e7edb7136456  v2-design/DEVIATIONS.md
380f749df195901820d56507a8e1832e31516e1e121a314f7e15b83cd06b32cb  v2-design/m1b-gate-artifact.json
b13523ce0dca251f9374b899542eb1cd61e7070c70d1fbb5b5f5638d3b58e3ff  v2-design/m1b-gate-artifact-hotel.json
1b86b219757e7a2b6e507c1e39b688d5b5cacf0366a1f4128a4c2ebfda89ad27  chaosprobe/scripts/m2_aa_analysis.py
2feafe3a6da2c0c6afba10de9cdd1dc4458c2cb72e050fd9fc502160dd25f060  chaosprobe/scripts/aa_block.py
```

(`01-PREREGISTRATION.md`'s hash is its content at the moment of staging the
freeze commit; this manifest does not checksum itself.)

## SHA-256 — raw A/A session summaries (Zenodo upload)

```
0f183820802bfbab28ae41e9b2700e5f5dacce28ab81c80cd23f3968fdd8f318  v2-aa/20260611-184530/summary.json
4e824450e0e270c034d2def2cd78f06dccf838b739f37f19a7d78b7cbffc32ad  v2-aa/20260611-213923/summary.json
cfd02c1e5344b9ddd00013ca5aa240f1567372b4b94336bf318972d3782e56d2  v2-aa/20260612-002516/summary.json
e8851d6aabb02178ee3fdaf3674e5b42c05c21b98d6cf85bdd7a16f579fbd6eb  v2-aa/20260612-030816/summary.json
c234df92d322499accb3b45078fb885851c5d79a96889ab9d9b4edf68ef347df  v2-aa/20260612-074544/summary.json
d682101be7b26536aa3958731e541c88ffa0ddc12228b9b05cf7f2fcb95e5123  v2-aa/20260612-103215/summary.json
```

(Per-condition `f-*.json` raws — 30 files — are bundled alongside each
`summary.json`; their checksums travel inside the deposit tarball's own
`SHA256SUMS` file, generated when the bundle is built.)

## Depositing (user-owned — outward-facing, mints the DOI)

The committed state is pushed and tagged. The raw-data tarball is staged at:

```
/tmp/v2-prereg-freeze-deposit.tar.zst   (built at freeze time; see below)
```

To publish:

1. Create a new Zenodo deposit (or a new version under the existing v1
   concept DOI 10.5281/zenodo.20639146 if you want them linked).
2. Upload: the raw-data tarball **and** a clone/export of the repo at the
   `v2-prereg-freeze` tag (or just link the GitHub release of that tag —
   Zenodo–GitHub integration archives a tagged release automatically).
3. Title: “ChaosProbe v2 — pre-registration (frozen) + M2 A/A calibration
   data”. Set the publication date to the freeze date (2026-06-13).
4. Publish to mint the DOI, then record it in the **DOI** line above and in
   the prereg header, and commit that one-line update.

Until the DOI is minted, the **git tag `v2-prereg-freeze` is the immutable
freeze reference**; the DOI adds third-party, citable permanence.
