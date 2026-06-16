#!/usr/bin/env bash
# C2 / V2-H3 re-run on the round-robin packed instrument (PRs #293, #294, #295).
# 3 cells × 8 replicate sessions = 24 node-drain sessions into results/c2-roundrobin/.
# Blocked by cell, matching the c2-rerun2 structure. Each session: 1 condition
# (f-050), 1 iteration, --v2-packed-assignment round-robin.
set -u

export KUBECONFIG="$HOME/.kube/config-chaosprobe"
# ⛔ Safety gate — never run chaos against anything but the thesis cluster.
ctx="$(kubectl config current-context 2>/dev/null)"
if [ "$ctx" != "kubernetes-admin@cluster.local" ]; then
  echo "SAFETY GATE FAIL: context='$ctx' (expected kubernetes-admin@cluster.local)" >&2
  exit 1
fi
echo "SAFETY GATE OK: $ctx"

OUT="results/c2-roundrobin"
WORKERS="worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8"

run_cell() {
  local r="$1" mode="$2"
  for i in $(seq 1 8); do
    echo "=== CELL r=$r mode=$mode session $i/8 ($(date -u +%H:%M:%S)) ==="
    uv run chaosprobe run -n online-boutique \
      -e scenarios/online-boutique/node-drain.yaml \
      --v2-levels 0.5 --v2-replicas "$r" --v2-mode "$mode" \
      --v2-packed-assignment round-robin \
      --v2-workers "$WORKERS" --v2-solver-seed 0 --v2-order-seed 1 \
      -i 1 -o "$OUT" || echo "RUN FAILED: r=$r mode=$mode i=$i"
  done
}

run_cell 1 packed
run_cell 3 packed
run_cell 3 anti-affine
echo "=== CAMPAIGN COMPLETE ($(date -u +%H:%M:%S)) ==="
