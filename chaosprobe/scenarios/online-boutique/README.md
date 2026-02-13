# Online Boutique Chaos Experiments

Chaos experiments for [Google's Online Boutique](https://github.com/GoogleCloudPlatform/microservices-demo) (microservices-demo), a 11-service e-commerce application. Experiments are organized by the distributed systems performance bottleneck taxonomy:

```
Performance
├── Execution
│   ├── Saturation (Stack Height / QoS)    → CPU hog, Memory hog
│   └── Contention (Critical Path)         → Pod delete on orchestrator
└── I/O
    ├── Contention (Critical Sections)     → Redis latency (in-cluster storage)
    └── Saturation (Bandwidth)             → Network loss, Disk I/O stress
```

## Architecture

```
                        ┌─────────────┐
                   ─────│  frontend    │─────
                  │     │  (Go:8080)   │     │
                  │     └──────────────┘     │
                  │            │             │
         ┌────────┴──┐  ┌─────┴──────┐  ┌───┴──────────┐
         │ adservice  │  │ checkout   │  │ recommendation│
         │(Java:9555) │  │(Go:5050)   │  │ (Python:8080) │
         └────────────┘  └─────┬──────┘  └───┬───────────┘
                               │             │
              ┌────────┬───────┼──────┬──────┘
              │        │       │      │
         ┌────┴───┐┌───┴──┐┌──┴──┐┌──┴────────┐
         │currency││cart  ││ship ││productcat  │
         │(Node:  ││(C#:  ││(Go: ││(Go:3550)   │
         │ 7000)  ││7070) ││50051│└────────────┘
         └────────┘└──┬───┘└─────┘
                      │
                 ┌────┴────┐        ┌──────────┐  ┌─────────┐
                 │redis-cart│        │ payment  │  │ email   │
                 │(:6379)   │        │(Node:    │  │(Py:8080)│
                 └──────────┘        │ 50051)   │  └─────────┘
                                     └──────────┘
```

## Scenarios

### 1. Deploy (Health Check)

**Directory:** `deploy/`

Deploys all 11 microservices + Redis + load generator, then runs a simple pod-delete on the frontend as a deployment health check.

```bash
chaosprobe run scenarios/online-boutique/deploy/
```

### 2. CPU Contention — Execution Saturation

**Directory:** `contention-cpu/`
**Target:** currencyservice (highest QPS, Node.js single-threaded)
**Experiment:** `pod-cpu-hog` — 100% CPU load for 60s

Tests cascading latency when the hottest service's CPU is saturated. Currency conversion is called by checkout, frontend, and every price display.

```bash
chaosprobe run scenarios/online-boutique/contention-cpu/
```

### 3. Memory Contention — Execution Saturation

**Directory:** `contention-memory/`
**Target:** recommendationservice (Python, loads catalog into memory)
**Experiment:** `pod-memory-hog` — 300MB consumption for 60s

Tests OOMKill behavior and graceful degradation when a Python service exceeds memory limits.

```bash
chaosprobe run scenarios/online-boutique/contention-memory/
```

### 4. Scheduling Contention — Critical Path

**Directory:** `contention-scheduling/`
**Target:** checkoutservice (orchestrator calling 5+ services)
**Experiment:** `pod-delete` — force-kill 100% of pods every 15s for 60s

Tests pod scheduling recovery time on the critical checkout path. With 1 replica and force-delete, the entire checkout flow breaks until rescheduling completes.

```bash
chaosprobe run scenarios/online-boutique/contention-scheduling/
```

### 5. Redis Latency — I/O Contention (In-cluster Storage)

**Directory:** `contention-redis-latency/`
**Target:** cartservice (C#, depends on redis-cart for session state)
**Experiment:** `pod-network-latency` — 300ms latency to Redis for 60s

Tests in-cluster storage contention by injecting latency between the cart service and its Redis dependency. Measures whether slow storage cascades to checkout and frontend.

```bash
chaosprobe run scenarios/online-boutique/contention-redis-latency/
```

### 6. Network Loss — I/O Saturation (Bandwidth)

**Directory:** `contention-network-loss/`
**Target:** checkoutservice (orchestrator on critical path)
**Experiment:** `pod-network-loss` — 60% packet loss for 60s

Tests bandwidth saturation and network congestion on the checkout service. With 60% packet loss, downstream calls to payment, shipping, email, cart, and currency fail intermittently.

```bash
chaosprobe run scenarios/online-boutique/contention-network-loss/
```

### 7. Disk I/O Stress — I/O Saturation

**Directory:** `contention-io-stress/`
**Target:** productcatalogservice (Go, reads product data from embedded JSON)
**Experiment:** `pod-io-stress` — 80% filesystem utilization for 60s

Tests disk I/O pressure on the product catalog. Measures whether the service degrades gracefully or becomes unresponsive, affecting frontend product listing and recommendations.

```bash
chaosprobe run scenarios/online-boutique/contention-io-stress/
```

## Workflow

### Step 1: Deploy the application

```bash
chaosprobe run scenarios/online-boutique/deploy/
```

This deploys all services to the `online-boutique` namespace and verifies the deployment with a frontend health check.

### Step 2: Run contention experiments

Each experiment-only scenario targets already-deployed services:

```bash
# Pick one:
chaosprobe run scenarios/online-boutique/contention-cpu/
chaosprobe run scenarios/online-boutique/contention-memory/
chaosprobe run scenarios/online-boutique/contention-scheduling/
chaosprobe run scenarios/online-boutique/contention-redis-latency/
chaosprobe run scenarios/online-boutique/contention-network-loss/
chaosprobe run scenarios/online-boutique/contention-io-stress/
```

### Step 3: AI fix-and-verify loop

Feed the JSON output to an AI agent. The output includes:
- Full YAML content of all manifests and experiments
- LitmusChaos experiment verdicts (Pass/Fail)
- Probe results (frontend HTTP availability during chaos)

The AI reads the output, diagnoses the root cause, modifies the deployment manifests or experiment parameters, and re-runs to verify the fix.

### Step 4: Cleanup

```bash
chaosprobe cleanup --namespace online-boutique
```

## Cluster Requirements

- 2+ worker nodes with at least 2GB RAM each
- LitmusChaos operator (litmus-core chart)
- containerd runtime (kubespray default)
- Available LitmusChaos experiments: pod-delete, pod-cpu-hog, pod-memory-hog, pod-network-latency, pod-network-loss, pod-io-stress

## Tuning Parameters

**Load patterns** — Modify `loadgenerator.yaml`:
- `USERS=10` (default balanced load)
- `USERS=50` (high load)
- `USERS=100` (burst/stress)

**Replicas** — Increase replicas on target services to test scheduling under contention:
- `replicas: 1` (default, worst case)
- `replicas: 3` (tests pod distribution and PDB effectiveness)

**Chaos intensity** — Adjust experiment parameters:
- `CPU_LOAD`, `MEMORY_CONSUMPTION` — severity of resource pressure
- `NETWORK_LATENCY`, `NETWORK_PACKET_LOSS_PERCENTAGE` — I/O degradation level
- `PODS_AFFECTED_PERC` — fraction of pods hit simultaneously
- `TOTAL_CHAOS_DURATION` — how long the disruption lasts
