#!/usr/bin/env bash
# AX availability-axis pre-registration — node-drain A/A CALIBRATION block.
# Purpose: measure the availability-face variance components under node-drain at
# FIXED placement, to fill the AX prereg TBDs (AX-H1 SESOI, per-cell n,
# AX-H4 deltas) and confirm AX-H3's fixed-placement test-retest design yields
# non-degenerate, noise-like within-condition (run-to-run) variance.
#
# Design (per AX-PREREGISTRATION-DRAFT.md §5): identical-placement node-drain
# session pairs — same --v2-solver-seed (=> identical placements every session),
# only --v2-order-seed varies. Complete-block (all 5 f-levels) so each session
# replicates all 5 fixed placements. 6 sessions = 3 pairs. NULL calibration data,
# not confirmatory: nothing here tests a registered hypothesis.
set -u

export KUBECONFIG="$HOME/.kube/config-chaosprobe"
# ⛔ Safety gate — never run chaos against anything but the thesis cluster.
ctx="$(kubectl config current-context 2>/dev/null)"
if [ "$ctx" != "kubernetes-admin@cluster.local" ]; then
  echo "SAFETY GATE FAIL: context='$ctx' (expected kubernetes-admin@cluster.local)" >&2
  exit 1
fi
echo "SAFETY GATE OK: $ctx"

OUT="results/ax-aa-nodedrain"
WORKERS="worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8"

for s in $(seq 1 6); do
  echo "=== AX-AA node-drain session $s/6 (solver-seed=0 FIXED, order-seed=$s) ($(date -u +%H:%M:%S)) ==="
  uv run chaosprobe run -n online-boutique \
    -e scenarios/online-boutique/node-drain.yaml \
    --v2-levels 0,0.25,0.5,0.75,1.0 --v2-replicas 1 \
    --v2-workers "$WORKERS" --v2-solver-seed 0 --v2-order-seed "$s" \
    -i 3 -o "$OUT" || echo "RUN FAILED: AX-AA session $s"
done
echo "=== AX-AA NODE-DRAIN CALIBRATION BLOCK COMPLETE ($(date -u +%H:%M:%S)) ==="
