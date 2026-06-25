#!/usr/bin/env bash
# C1 / V2-H1 dose-response — EXTERNAL VALIDITY replication on hotelReservation.
# Mirrors the frozen online-boutique C1 design (C1-OB-REPORT.md): complete-block,
# every session visits all five cross-node fractions f ∈ {0,0.25,0.5,0.75,1.0} in
# randomized order (recorded seed), r=1, pod-delete churn + host-side Locust.
# 8 sessions (order-seeds 1-8, solver-seed 0), 5 levels × 3 iterations.
# Exploratory — reported OUTSIDE the frozen Holm family.
#
# Gate flags: hotelReservation needs a wget-capable probe pod (fixed in #322) and
# sustained warm-up load through the readiness gate (#317/#318/#321) so the gate
# passes untainted on its Consul/gRPC stack. Validated live: gate passes ~57s, 0 taints.
set -u

export KUBECONFIG="$HOME/.kube/config-chaosprobe"
# ⛔ Safety gate — never run chaos against anything but the thesis cluster.
ctx="$(kubectl config current-context 2>/dev/null)"
if [ "$ctx" != "kubernetes-admin@cluster.local" ]; then
  echo "SAFETY GATE FAIL: context='$ctx' (expected kubernetes-admin@cluster.local)" >&2
  exit 1
fi
echo "SAFETY GATE OK: $ctx"

OUT="results/c1-hotel"
WORKERS="worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8"

for s in $(seq 1 8); do
  echo "=== C1-HOTEL session $s/8 (order-seed=$s) ($(date -u +%H:%M:%S)) ==="
  uv run chaosprobe run -n hotel-reservation \
    -e scenarios/hotel-reservation/pod-delete.yaml \
    --v2-levels 0,0.25,0.5,0.75,1.0 --v2-replicas 1 \
    --v2-workers "$WORKERS" --v2-solver-seed 0 --v2-order-seed "$s" \
    --gate-sustained-load --gate-load-concurrency 6 --pre-gate-warmup 30 \
    -i 3 -o "$OUT" || echo "RUN FAILED: C1 session $s"
done
echo "=== C1-HOTEL CAMPAIGN COMPLETE ($(date -u +%H:%M:%S)) ==="
