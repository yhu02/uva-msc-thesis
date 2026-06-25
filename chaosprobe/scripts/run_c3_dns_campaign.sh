#!/usr/bin/env bash
# C3 / H2 campaign — placement-dependence + DNS intervention (PRs #300/#301/#302).
# Design: cache {on,off} × f {0,1} × r=1, pod-delete churn + host-side Locust.
# A SESSION is a fixed cache mode visiting BOTH f-levels (f-000 packed, f-100
# spread) as conditions, so it yields a packed AND a spread UDP-conntrack drop.
# Sessions are run in matched PAIRS (one cache-off, one cache-on) with the
# within-pair cache order RANDOMIZED from a recorded seed; pairs run
# sequentially, so the analysis (scripts/c3_h2_dns.py) recovers each pair by
# timestamp order within each cache group regardless of within-pair order.
#
# Analysis pairing/tests: c3_h2_dns.py — (a) cache-off spread>packed paired
# Wilcoxon; (b) per-pair spread cache-on-vs-off shrinkage ≥ 50% one-sided
# Wilcoxon; conjunction = (a) AND (b).
#
# ⚠️ Before the full run, do the go/no-go SMOKE + cache-state verify (one pair):
#   1. run a single cache-off and cache-on spread session,
#   2. confirm the toggle changed the DNS path — cache-off pods resolve via the
#      CoreDNS clusterIP (cross-node UDP, larger UDP pool) and cache-on via the
#      node-local cache (169.254.25.10, minimal cross-node UDP) — visible in the
#      per-node conntrack UDP samples and the CoreDNS cache-hit Prometheus metric,
#   3. confirm the spread cache-on UDP drop is materially (≥~50%) below cache-off.
#   Only if the shrinkage is detectable is the full campaign worth its ~3 h/session.
#
# ⚠️ A dnsConfig-realization DEVIATION must be logged
# before this campaign's results are quoted (NodeLocal DNSCache realized via pod
# dnsConfig rather than the kubelet --cluster-dns default; see C3-OB-SCOPE.md).
#
# n = PAIRS (default 7, the M2 power floor for H2(b); n=11 covers the
# 60%-shrinkage case — bump PAIRS to 11 for that). ~3 h/session ⇒ days of cluster
# time; launch in the background and poll.
set -u

export KUBECONFIG="$HOME/.kube/config-chaosprobe"
# ⛔ Safety gate — never run chaos against anything but the thesis cluster.
ctx="$(kubectl config current-context 2>/dev/null)"
if [ "$ctx" != "kubernetes-admin@cluster.local" ]; then
  echo "SAFETY GATE FAIL: context='$ctx' (expected kubernetes-admin@cluster.local)" >&2
  exit 1
fi
echo "SAFETY GATE OK: $ctx"

OUT="results/c3-dns"
WORKERS="worker1,worker2,worker3,worker4,worker5,worker6,worker7,worker8"
PAIRS="${C3_PAIRS:-7}"          # matched cache-off/cache-on pairs (override: C3_PAIRS=11)
ITERS="${C3_ITERS:-3}"         # iterations per f-level (the C3 driver medians over them)
ORDER_SEED="${C3_ORDER_SEED:-20260617}"  # recorded seed for the randomized within-pair cache order

# Reproducible randomized within-pair cache order: the first-mover per pair.
ORDER=$(python3 -c "import random; random.seed($ORDER_SEED); print(' '.join(random.choice(['off','on']) for _ in range($PAIRS)))")
echo "C3: $PAIRS pairs, iters=$ITERS, cache-order seed=$ORDER_SEED, first-movers: $ORDER"

run_session() {
  local cache="$1" pair="$2"
  echo "=== PAIR $pair/$PAIRS cache=$cache ($(date -u +%H:%M:%S)) ==="
  # f-solver placement (default --packed-assignment solver) hits f=0 (packed)
  # and f=1 (spread) at r=1; --dns-cache toggles the DNS resolver per session.
  uv run chaosprobe run -n online-boutique \
    -e scenarios/online-boutique/pod-delete.yaml \
    --fraction-levels 0,1 --replica-degree 1 --placement-mode packed \
    --dns-cache "$cache" \
    --worker-nodes "$WORKERS" --solver-seed 0 --order-seed 1 \
    -i "$ITERS" -o "$OUT" || echo "RUN FAILED: pair=$pair cache=$cache"
}

pair=0
for first in $ORDER; do
  pair=$((pair + 1))
  second=$([ "$first" = "off" ] && echo "on" || echo "off")
  run_session "$first" "$pair"
  run_session "$second" "$pair"
done
echo "=== C3 CAMPAIGN COMPLETE ($(date -u +%H:%M:%S)) ==="
