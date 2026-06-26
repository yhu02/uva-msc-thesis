# Conntrack protocol-composition probe (2026-06-10)

Decomposes the H2 conntrack signal by protocol, answering which entries
actually change during the `pod-delete` kill cycle — kube-proxy's active
cleanup is UDP-only upstream, while the workload's east-west traffic is
gRPC/TCP (see `references.md` §4 and the discussion chapter,
`thesis/latex/chapters/06-discussion.tex`).

## Method

- `probe-pods.yaml` — four `hostNetwork` alpine pods (one per worker,
  privileged) with `conntrack-tools` installed; each reads its **host's**
  conntrack table via the netlink CLI (`conntrack -L`). The deprecated
  `/proc/net/nf_conntrack` interface is not compiled into this kernel.
  Toolchain note: `conntrack-tools` was installed unpinned from Alpine 3.20
  repositories on 2026-06-10 (the resolved version was not recorded); pin
  the package or bake an image for an exact re-run.
- `sampler.sh` — every 5 s, per worker: `conntrack -L | awk '{print $1}'
  | sort | uniq -c` → `conntrack_proto_samples.csv` (`ts,node,proto,count`,
  one continuous stream 19:59:21→20:22:35 UTC covering both runs).
- Two real ChaosProbe runs provided the kill cycles while the sampler ran:
  `pod-delete` × *i* = 1 under `spread` (`results/20260610-195929`, archived
  `run-20260610-200013`) and under `colocate` (`results/20260610-201052`,
  archived `run-20260610-201131`). The chaos windows (from each run's
  `summary.json` `anomalyLabels[0]`) are shipped in `windows.csv` so the CSV
  can be aligned without the archives.

## Result (cluster-total across the 4 workers; reproducible from the CSV + windows.csv)

**Window-design caveat first:** with *i* = 1, the 120 s pre-chaos baseline
contains the Locust start-up ramp — under `spread`, UDP transiently spiked
to 5,485 entries inside that window. Pre-chaos means are therefore
ramp-contaminated and this probe **cannot** decompose the campaign's flush
percentages (H2's 38.5 % vs 2.7 % medians come from the seven-session
campaign, not from here). What the probe measures robustly is composition
and event timing **within** the chaos windows:

| quantity (chaos-window) | spread | colocate |
|---|---|---|
| UDP entries, median | **910** | **224** |
| TCP entries, median | 4,197 | 3,965 |
| TCP drop at first kill cycle | 5,935 → 4,253 (**−28 %**) | 4,973 → 3,937 (**−21 %**) |

Three composition findings:

1. **TCP dominates the table under both placements** (≈80 %+ of entries at
   steady points) — and TCP entries **visibly drop at the kill cycles in
   both placements**. Since kube-proxy never actively flushes TCP
   (kubernetes/kubernetes #100698, #104098), these drops are **kernel-side
   teardown** of flows traversing the killed pod (RST/CNI cleanup/expiry).
2. **Under steady load, `spread` sustains ~4× more UDP (DNS) entries than
   `colocate`** (medians 910 vs 224) — the placement-dependent component,
   consistent with cross-node calls driving connection churn and DNS
   re-resolution, and with kube-proxy's UDP-only cleanup having
   correspondingly more to clean under spread.
3. `colocate`'s UDP count *rises* during chaos (~72 → 230–390): restart
   churn drives DNS lookups even when steady-state DNS traffic is minimal.

Interpretation for H2: **both candidate mechanisms are visible** — kernel
TCP teardown carries the sharp kill-cycle drops; the UDP/DNS pool is the
clearly placement-dependent component. A single iteration per placement
with a ramping baseline cannot apportion the campaign's aggregate flush
between them; treat this probe as composition + event-timing evidence only.

Cluster: k8s v1.28.6, kube-proxy ipvs mode.
