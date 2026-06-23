# ChaosProbe C4 — node-drain dose-response (design-corrected re-analysis)

Raw, tamper-evident run archive for campaign **C4**, the exploratory
design-corrected re-analysis of the availability axis in the MSc thesis
*"Measuring Placement-Sensitive Resilience under Chaos: A Pre-Registered,
Layered Study in Kubernetes"* (Yvo Hu, University of Amsterdam, 2026).

Code, manuscript, and analysis scripts:
https://github.com/yhu02/uva-msc-thesis

## What this is

C4 is **exploratory and outside the frozen confirmatory Holm family**. It
recomputes the three construction-limited availability-side results (H3 trough
depth, H4 frontier, H5 availability sub-score) under `node-drain`, whose blast
radius is placement-dependent — unlike the confirmatory `pod-delete` regime,
where removing a service's only replica makes the availability trough ≈ 1 pod
for every placement. Its corrected criteria were **pre-declared** (in
`v2-design/DESIGN-FIX-SCOPE.md`) before the new data were examined — a
design-corrected analogue of the deposit-before-analysis rule.

The result: the availability trough varies monotonically with placement
(1.00 packed → 0.36 spread), a genuine latency × availability trade-off appears,
and the replication-rescue effect is real once the 1-pod margin artifact is
removed. The arm does **not** re-open the frozen confirmatory verdicts; see
§ "Design-corrected re-analysis" in the thesis results chapter.

## Design

- 8 complete-block sessions, `node-drain` at five cross-node fraction levels
  `f ∈ {0, 0.25, 0.5, 0.75, 1.0}`, replication `r = 1`, 5 × 3 = 15 iterations
  per session.
- Workload: Google Online Boutique (`online-boutique`), eight 2-vCPU/4-GiB
  workers, Kubernetes v1.28.6, kube-proxy ipvs, containerd 1.7.11, Calico CNI.
- All 8 sessions pass `doctor --strict` (clean tree, scenario hash present,
  `runMetadata` present, `git.dirty: false`). Sessions 1–2 on commit `445c5bc`,
  3–8 on commit `71c545d` — identical run-path code (the intervening merge was
  thesis-docs only).

## Contents

- `results/c4-nodedrain-dose/<timestamp>/` — one directory per session, each with
  raw `summary.json` (per-iteration metrics, placements, EndpointSlice snapshots,
  provenance fingerprint), per-level JSON (`f-*.json`), and `charts/`.
- `c4-nodedrain-manifest.sha256` — the frozen **pre-analysis** SHA-256 integrity
  manifest (the 56 per-level result JSONs), computed before the design-fix
  analysis was written.

## Verification

```bash
tar -xzf c4-nodedrain-dose.tar.gz
sha256sum -c c4-nodedrain-manifest.sha256          # 56/56 OK
# then, from the GitHub repo:
uv run chaosprobe doctor -s results/c4-nodedrain-dose/<session>/summary.json --strict
uv run python scripts/design_fix_analysis.py       # reproduces the corrected numbers
```

## Related records

- Concept record (all ChaosProbe datasets): DOI 10.5281/zenodo.20639145 — this
  deposit `continues` it.
- Frozen pre-registration: DOI 10.5281/zenodo.20690836 — this deposit
  `isSupplementTo` it.
- Confirmatory campaigns C1/C2/C3: DOIs 10.5281/zenodo.20690737, 20726729,
  20748970 — referenced; the H3 correction re-uses the deposited C2 node-drain
  data (no new run for that face).

License: CC-BY-4.0.
