#!/usr/bin/env bash
# C4 / design-corrected — node-drain DOSE-RESPONSE on online-boutique.
# Fixes the degenerate availability face of H4 (and the no-signal H5 availability
# sub-score): node-drain produces a real, placement-dependent outage (blast radius
# = services co-located with the drain target), so the availability axis VARIES
# across the cross-node fractions where pod-delete left it constant.
# Same complete-block design as C1, fault swapped pod-delete -> node-drain:
# f in {0,0.25,0.5,0.75,1.0}, r=1, 8 sessions (order-seeds 1-8, solver-seed 0),
# 5 levels x 3 iterations. Exploratory, outside the frozen Holm family.
# Criteria pre-declared in v2-design/DESIGN-FIX-SCOPE.md.
set -u

export KUBECONFIG="$HOME/.kube/config-chaosprobe"
# ⛔ Safety gate — never run chaos against anything but the thesis cluster.
ctx="$(kubectl config current-context 2>/dev/null)"
if [ "$ctx" != "kubernetes-admin@cluster.local" ]; then
  echo "SAFETY GATE FAIL: context='$ctx' (expected kubernetes-admin@cluster.local)" >&2
  exit 1
fi
echo "SAFETY GATE OK: $ctx"

OUT="results/c4-nodedrain-dose"
WORKERS="worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8"

for s in $(seq 1 8); do
  echo "=== C4-NODEDRAIN-DOSE session $s/8 (order-seed=$s) ($(date -u +%H:%M:%S)) ==="
  uv run chaosprobe run -n online-boutique \
    -e scenarios/online-boutique/node-drain.yaml \
    --v2-levels 0,0.25,0.5,0.75,1.0 --v2-replicas 1 \
    --v2-workers "$WORKERS" --v2-solver-seed 0 --v2-order-seed "$s" \
    -i 3 -o "$OUT" || echo "RUN FAILED: C4 session $s"
done
echo "=== C4-NODEDRAIN-DOSE CAMPAIGN COMPLETE ($(date -u +%H:%M:%S)) ==="
