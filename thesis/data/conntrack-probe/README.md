# Conntrack protocol-composition probe (2026-06-10)

Decomposes the H2 conntrack "flush" by protocol, answering which entries
actually disappear during the `pod-delete` kill cycle — kube-proxy's active
cleanup is UDP-only upstream, while the workload's east-west traffic is
gRPC/TCP (see `references.md` §4 and `thesis/06-discussion.md` §6.3).

## Method

- `probe-pods.yaml` — four `hostNetwork` alpine pods (one per worker,
  privileged) with `conntrack-tools` installed; each reads its **host's**
  conntrack table via the netlink CLI (`conntrack -L`). The deprecated
  `/proc/net/nf_conntrack` interface is not compiled into this kernel.
- `sampler.sh` — every 5 s, per worker: `conntrack -L | awk '{print $1}'
  | sort | uniq -c` → `conntrack_proto_samples.csv` (`ts,node,proto,count`).
- Two real ChaosProbe runs provided the kill cycles while the sampler ran:
  `pod-delete` × *i* = 1 under `spread` (`results/20260610-195929`, archived
  `run-20260610-200013`) and under `colocate` (`results/20260610-201052`,
  archived `run-20260610-201131`). Chaos windows for alignment come from each
  run's `summary.json` `anomalyLabels[0].startTime/endTime`.

## Result (cluster-total across the 4 workers)

| placement | pre-chaos TCP / UDP | UDP during kill cycle | TCP during kill cycle |
|---|---|---|---|
| spread | 3,857 / 1,822 (32 %) | −50 to −58 % | grows +6 to +16 % |
| colocate | 2,993 / 72 (2 %) | tiny pool (noise) | grows |

The placement-dependent H2 signal is the **UDP (DNS) standing pool** —
~25× larger under spread — collapsing when kube-proxy's UDP-only cleanup
fires on the endpoint change. TCP entries never flush and grow with
reconnect churn, matching kubernetes/kubernetes #100698 / #104098.

Single iteration per placement: quote for composition and direction, not
magnitudes. Cluster: k8s v1.28.6, kube-proxy ipvs mode.
