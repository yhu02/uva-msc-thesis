#!/usr/bin/env bash
# Samples per-node conntrack protocol composition every 5s into a CSV.
# Usage: conntrack_probe_sampler.sh <out.csv>   (runs until killed)
set -u
export KUBECONFIG="$HOME/.kube/config-chaosprobe"
OUT="${1:-/tmp/conntrack_proto_samples.csv}"
[ -f "$OUT" ] || echo "ts,node,proto,count" > "$OUT"
while true; do
  ts=$(date -u +%FT%T)
  for n in worker1 worker2 worker3 worker4; do
    kubectl exec -n default "ct-probe-$n" -- conntrack -L 2>/dev/null \
      | awk '{print $1}' | sort | uniq -c \
      | awk -v ts="$ts" -v n="$n" '{print ts "," n "," $2 "," $1}' >> "$OUT"
  done
  sleep 5
done
