# DeathStarBench hotelReservation — ChaosProbe's second workload

The second workload pinned by [docs/design/00-DESIGN.md §7](../../../docs/design/00-DESIGN.md):
DSB `hotelReservation` is the lightest DeathStarBench application and the only
realistic candidate at this cluster scale — a deliberately *different* topology
(deep frontend fan-out + per-service datastore pairs) and RPC stack (Go gRPC
with **consul-based discovery**: inter-service calls go pod-to-pod via
consul-resolved IPs, not ClusterIP Services) from Online Boutique. Capacity and
the fraction-solver gate for this workload are **M2 exit criteria**;
under the de-scope order, if it fails either gate the second-workload claim is dropped
**first**, before the iptables arm.

## Provenance

- **Upstream:** [delimitrou/DeathStarBench](https://github.com/delimitrou/DeathStarBench),
  `hotelReservation/kubernetes/`, pinned at commit
  [`6ecb0970`](https://github.com/delimitrou/DeathStarBench/tree/6ecb09706140f8730b5385c08f1386c654c3c526/hotelReservation)
  (2024-06-27 — the most recent commit touching `hotelReservation`).
- **Images** (all pinned by tag; upstream uses `latest` except mongo):
  - `deathstarbench/hotel-reservation:0.3.5` — single multi-binary image for all
    8 app services (Docker Hub; pushed 2024-06-27, byte-identical to `latest` as
    of the pinned commit date). The image bakes in upstream `config.json`, whose
    service addresses (`consul:8500`, `jaeger:6831`, `mongodb-*:27017`,
    `memcached-{profile,rate}:11211`, `memcached-reserve:11211`) must match the
    Service names in `deploy/` — they do.
  - `mongo:4.4.6` (upstream-pinned), `memcached:1.6.42-alpine`,
    `hashicorp/consul:1.18.2`, `jaegertracing/all-in-one:1.58.1` (the latter
    three pinned by us; upstream floats `latest` — pins chosen as the releases
    current at the pinned commit date, except memcached where the current
    stable is used).
- **Adaptations** (per-file header comments carry the same list):
  - `io.kompose.service` labels → `app:` labels equal to the Deployment name —
    the label the ChaosProbe probers and ChaosEngine `applabel` selectors key on.
  - Explicit `resources.requests` (and limits) on every container; upstream sets
    CPU only. Conservative 50–100m / 64–128Mi per container (see budget below).
  - tcpSocket/httpGet readiness probes added (upstream has none).
  - MongoDB hostPath PV/PVCs replaced with `emptyDir` — chaos runs need no
    persistence; every service re-seeds its database on startup.
  - Upstream's `memcached-reservation` Deployment is renamed to
    `memcached-reserve` to match its own Service name (the name the baked-in
    config dials), keeping Deployment name == `app` label == Service name.
  - kompose/istio annotations dropped; `frontend-external` NodePort added for
    host-side load generation; jaeger gets `MEMORY_MAX_TRACES=10000`.
  - Single replica everywhere (placement experiments set r explicitly).
- **Known caveat (verified in upstream source, untested live):** at the pinned
  commit the frontend also initializes `srv-review` / `srv-attractions` gRPC
  clients, but upstream's `kubernetes/` manifests ship no such services
  (docker-compose-only additions). gRPC dialing is lazy and no deployed
  endpoint exercises those routes, so the upstream k8s deployment is shipped
  as-is; confirm at first live deploy that the frontend goes Ready.

## Deploy

```bash
# Namespace comes from deploy/experiment.yaml's appinfo.appns: hotel-reservation
uv run chaosprobe provision scenarios/hotel-reservation/deploy/
# or: kubectl create ns hotel-reservation && kubectl apply -n hotel-reservation -f scenarios/hotel-reservation/deploy/
```

19 Deployments: 8 app services (`frontend:5000` HTTP; `profile:8081`,
`search:8082`, `geo:8083`, `rate:8084`, `recommendation:8085`, `user:8086`,
`reservation:8087` gRPC), 6 MongoDBs, 3 memcacheds, plus `consul` and `jaeger`.

## Capacity budget (DESIGN §7.1 method, on the adopted M0 fallback N = 8 × 4 GiB)

Sums of the manifests' `resources.requests` (the §7.1 method — re-measure live
with `kubectl get pods -n hotel-reservation -o json` at the M2 gate).
Allocatable per worker from the M1b gate artifact: 2000m CPU /
3,997,184,000 B ≈ 3812 Mi → **16,000m CPU / ~29.8 GiB across N = 8**.

| Cell | Pods | CPU requests | Memory requests | of allocatable |
|---|---|---|---|---|
| r = 1 (all 19 singleton) | 19 | 1350m | 1728 Mi | 8.4 % CPU / 5.7 % mem |
| r = 3 (8 app svcs ×3, datastores+infra ×1) | 35 | 2250m | 2752 Mi | 14.1 % CPU / 9.0 % mem |
| r = 3 worst case (all 19 ×3) | 57 | 4050m | 5184 Mi | 25.3 % CPU / 17.0 % mem |

**Verdict: PASS at r = 3 on N = 8.** Even the worst case leaves ≥ 74 % CPU and
≥ 83 % memory headroom before infrastructure overhead (Prometheus, Litmus,
registry, probers ≈ 2–3 GiB / 2–3 vCPU per DESIGN §7.1) — comfortably above
the ≥ 30 % headroom criterion. Anti-affinity at r = 3 needs 3 distinct
schedulable nodes per service; N = 8 satisfies this with slack. This static
arithmetic is the prep-window estimate; the binding check is the live M2 gate.

## Dependency graph / solver gate

[`topology.json`](topology.json) is the **static** service dependency graph
(19 services, 16 directed request-path edges; uniform weights), derived from
upstream source at the pinned commit (file-level citations inside). Load it
with `chaosprobe.placement.fraction_solver.load_static_topology(path)` — same
`(edges, services)` shape as `load_dependency_graph`, so `solve` /
`enumerate_reachable` consume it directly for the M2 solver gate. With 16
uniform edges the fraction quantum is 1/16 = 0.0625; the f
targets {0, .25, .5, .75, 1} are exact multiples. Consul/jaeger control-plane
chatter is excluded from the graph by design (documented in the file); the
measured route-view graph replaces this one once a hotel-reservation
`summary.json` exists.

## Load generation

Upstream ships **wrk2** Lua scripts
(`hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua`:
60 % `/hotels`, 39 % `/recommendations`, 0.5 % `/user` + 0.5 % `/reservation`
against `frontend:5000`). ChaosProbe's load story is **Locust from the host**
(DESIGN §4 — the host-side generator adds nothing in-cluster); a Locust
profile mirroring that endpoint mix is **TODO for M3** and deliberately not
part of this M2 prep deliverable. Until then, the deploy health check
(`deploy/experiment.yaml`) probes only frontend availability.

## De-scope rule

If the live M2 capacity check or solver gate fails for this workload, drop the
second-workload claim under the de-scope order (DESIGN §7: hotelReservation
goes **first**, before the iptables arm) — do not shrink the app or relax the
gate to force a fit.
