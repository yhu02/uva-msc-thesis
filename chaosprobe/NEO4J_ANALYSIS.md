# Neo4j Data Analysis — ChaosProbe Experiment Results

**Date**: 2026-04-15
**Database**: bolt://localhost:7687
**Total ChaosRuns**: 46 across 13 sessions

---

## 1. Data Overview

### 1.1 Graph Database Contents

| Node Type | Count | Description |
|---|---|---|
| K8sNode | 5 | Cluster nodes (1 control-plane + 4 workers) |
| Deployment | 12 | Online Boutique microservices |
| Service | 12 | Kubernetes service objects |
| ChaosRun | 46 | Individual experiment runs |
| PlacementStrategy | 6 | baseline, default, colocate, spread, antagonistic, random |
| RecoveryCycle | 165 | Pod deletion-to-ready recovery events |
| ExperimentResult | 46 | Per-run experiment outcomes |
| ProbeResult | 222 | HTTP health probe verdicts |
| MetricsPhase | 645 | Phase-aggregated metric summaries |
| PodSnapshot | 48 | Pod state at collection time |
| MetricsSample | 10,568 | Raw time-series data points |
| AnomalyLabel | 46 | Ground-truth fault injection labels |
| CascadeEvent | 46 | Fault propagation analysis |
| ContainerLog | 41 | Container log snapshots |

### 1.2 Sessions

| Session ID | Runs | Strategies | Status |
|---|---|---|---|
| 20260415-104513 | 6 | All 6 | Early run, baseline unstable |
| 20260415-113216 | 3 | baseline, default, colocate | Partial |
| 20260415-114317 | 2 | baseline, default | Partial |
| 20260415-115045 | 6 | All 6 | Complete, baseline scored 66 |
| 20260415-131517 | 1 | baseline | Single run |
| 20260415-132433 | 2 | baseline, default | Partial |
| 20260415-134002 | 1 | baseline | Single run |
| 20260415-135113 | 1 | baseline | Single run |
| 20260415-141340 | 6 | All 6 | **Broken**: Locust port-forward failed (100% error rate, 30s timeouts) |
| 20260415-155702 | 6 | All 6 | Clean, baseline PASS at 100 |
| 20260415-171636 | 6 | All 6 | Clean, baseline PASS at 100 |
| 20260415-182542 | 4 | baseline, default, colocate, spread | Clean, baseline PASS at 100 |
| 20260415-201025 | 2 | baseline, default | Clean, baseline PASS at 100 |

**Recommended clean sessions for analysis**: `155702`, `171636`, `182542` — baseline passes at 100%, infrastructure stable, load generation working.

**Exclude**: Session `141340` from load generation analysis (port-forward broken), and sessions `104513`, `113216`, `114317` where baseline scored 0 (infrastructure not yet stable).

---

## 2. Strategy Performance

### 2.1 Resilience Score by Strategy (5 complete sessions)

| Strategy | Scores | Avg | Verdicts |
|---|---|---|---|
| **baseline** | 0, 66, 100, 100, 100, 100, 100 | 93.2 (later: 100) | 4 PASS / 1 FAIL |
| **spread** | 66, 66, 66, 66, 16, 66 | 56.0 | 0 PASS / 6 FAIL |
| **random** | 66, 66, 0, 16, 66 | 42.8 | 0 PASS / 5 FAIL |
| **antagonistic** | 16, 16, 66, 16, 66 | 36.0 | 0 PASS / 5 FAIL |
| **default** | 0, 66, 16, 16, 66, 16, 16 | 32.8 | 0 PASS / 7 FAIL |
| **colocate** | 0, 0, 0, 0, 0, 0, 0 | 0.0 | 0 PASS / 7 FAIL |

### 2.2 Recovery Time by Strategy

Recovery time measures pod deletion-to-ready latency (milliseconds).

| Strategy | Mean | Median | Min | Max | P95 | Completed Cycles | Incomplete |
|---|---|---|---|---|---|---|---|
| **baseline** | 1,434 | 1,568 | 1,188 | 1,868 | 1,868 | 1 per run | 0 |
| **default** | 1,785 | 1,624 | 1,031 | 5,061 | 5,061 | 7 per run | 0 |
| **antagonistic** | 2,164 | 1,616 | 975 | 6,564 | 6,564 | 7 per run | 0 |
| **spread** | 3,072 | 2,926 | 857 | 10,018 | 10,018 | 7 per run | 0 |
| **random** | 2,860 | 2,157 | 867 | 11,709 | 11,709 | 7 per run | 0 |
| **colocate** | N/A | N/A | N/A | N/A | N/A | 0 | 1 per run |

Baseline runs only 1 deletion cycle (fault injection is disabled, but recovery watcher still captures the baseline engine's single pod event). All other strategies run 7 cycles over ~100s.

Colocate never completes a recovery cycle — the node is too overloaded to reschedule.

### 2.3 Load Generation Metrics (healthy sessions only)

All runs use the `steady` load profile against the frontend service.

| Strategy | Avg Error Rate | Avg Response (ms) | Avg P95 (ms) | Avg RPS |
|---|---|---|---|---|
| **baseline** | 15-18% | 70-160 | 190-590 | ~14.5 |
| **default** | 13-22% | 50-465 | 100-1,400 | 10-15 |
| **spread** | 17-30% | 130-275 | 580-1,600 | 13-15 |
| **antagonistic** | 14-25% | 180-360 | 260-1,000 | 10-14 |
| **random** | 15-29% | 130-320 | 410-970 | 14-15 |
| **colocate** | **77-93%** | 8-37 | 15-100 | ~15 |

Colocate has paradoxically low response times because most requests fail immediately (connection refused) rather than timing out.

### 2.4 Probe Results

Six HTTP probes are evaluated per run:

| Probe | Mode | What It Tests |
|---|---|---|
| frontend-healthz | Continuous | Basic liveness endpoint |
| frontend-cart | Continuous | Cart functionality (single service) |
| frontend-homepage-edge | Edge | Homepage at pre/post chaos only |
| frontend-homepage-tolerant | Continuous | Homepage with relaxed timeout |
| frontend-homepage-strict | Continuous | Homepage with strict timeout |
| frontend-product-strict | Continuous | Product page with strict timeout |

**Pass rates by probe across strategies (clean sessions)**:

| Probe | baseline | spread | random | default | antagonistic | colocate |
|---|---|---|---|---|---|---|
| frontend-healthz | 100% | 80% | 100% | 100% | 100% | 0% (N/A) |
| frontend-cart | 100% | 60% | 60% | 40% | 60% | 0% (N/A) |
| frontend-homepage-edge | 100% | 60% | 60% | 40% | 60% | 0% (Failed) |
| frontend-homepage-tolerant | 100% | 60% | 60% | 40% | 60% | 0% (N/A) |
| frontend-homepage-strict | 80% | 0% | 0% | 0% | 0% | 0% (N/A) |
| frontend-product-strict | 80% | 0% | 0% | 0% | 0% | 0% (N/A) |

The two strict probes (`homepage-strict`, `product-strict`) are the discriminating factor. They fail across every non-baseline strategy because the multi-service pages (product, homepage) call multiple backends and are sensitive to even brief disruptions during pod-delete cycles.

Colocate probes return `N/A` (never evaluated) because the ChaosEngine go-runner itself can't reach the frontend to execute probes. Only the Edge probe (pre/post chaos) attempts and fails.

---

## 3. Anomaly Tracing

### 3.1 Fault Injection Profile

Every run uses the identical fault:

| Field | Value |
|---|---|
| Fault type | `pod-delete` |
| Severity | `critical` |
| Category | `availability` |
| Resource | `pod` |
| Target service | `productcatalogservice` |
| Affected services | `frontend`, `checkoutservice`, `recommendationservice` |

For placement strategies, the anomaly label also records the target node:
- **colocate / spread**: `worker4`
- **antagonistic / random**: `worker1`
- **baseline / default**: no target node (default K8s scheduling)

### 3.2 Anomaly Timestamps (Session 171636 — Clean Reference)

| Strategy | Anomaly Start | Anomaly End | Duration | Target Node |
|---|---|---|---|---|
| baseline | 17:17:45 | 17:20:24 | 2m 39s | (default) |
| default | 17:21:40 | 17:25:21 | 3m 41s | (default) |
| colocate | 17:30:35 | 17:36:50 | 6m 15s | worker4 |
| spread | 17:39:18 | 17:46:02 | 6m 44s | worker4 |
| antagonistic | 17:48:50 | 17:52:32 | 3m 42s | worker1 |
| random | 17:54:35 | 17:58:13 | 3m 38s | worker1 |

Duration varies because the ChaosEngine runs probes before and after the fault window. Colocate/spread take longer because the go-runner times out on unreachable probes.

### 3.3 Recovery Cycle Timeline (Session 171636, Default Strategy)

Anomaly window: `17:21:40` to `17:25:21`. Seven pod-delete cycles at ~15s intervals:

| Cycle | Pod Deleted | Pod Scheduled | Pod Ready | Total Recovery | Sched Time | Startup Time |
|---|---|---|---|---|---|---|
| 0 | 17:22:16.79 | 17:22:19 | 17:22:18.94 | 2,155ms | 2,208ms | -52ms* |
| 1 | 17:22:35.70 | 17:22:36 | 17:22:36.77 | 1,073ms | 293ms | 779ms |
| 2 | 17:22:51.31 | 17:22:54 | 17:22:53.04 | 1,724ms | 2,680ms | -955ms* |
| 3 | 17:23:10.24 | 17:23:11 | 17:23:11.74 | 1,502ms | 753ms | 749ms |
| 4 | 17:23:25.85 | 17:23:28 | 17:23:28.00 | 2,155ms | 2,147ms | 7ms |
| 5 | 17:23:44.78 | 17:23:46 | 17:23:45.81 | 1,031ms | 1,211ms | -180ms* |
| 6 | 17:24:00.44 | 17:24:03 | 17:24:02.18 | 1,733ms | 2,550ms | -816ms* |

*Negative startup times: `scheduled_time` is from K8s events (rounded to whole seconds) while `ready_time` has millisecond precision. The pod becomes ready before the next second tick is recorded.

**Colocate** (same session): 1 cycle started (deletion at 17:31:11) but pod never recovered. `completed_cycles: 0, incomplete_cycles: 1`.

### 3.4 Resource Impact Correlation (Session 171636, Default Strategy)

Overlaying node resource time-series onto the recovery cycles (anomaly at 17:21:40-17:25:21, deletions start at 17:22:16):

```
Time                Phase         CPU(m)   CPU%    Mem%    Notes
17:21:22            pre-chaos     459      23.0%   74.4%   Steady state
17:21:37            pre-chaos     391      19.5%   74.0%   Steady state
17:21:42            during-chaos  391      19.5%   74.0%   Chaos declared, no deletions yet
17:22:06            during-chaos  642      32.1%   74.5%   CPU SPIKE (+65%) — pod churn begins
17:22:16            during-chaos  642      32.1%   74.5%   First pod-delete cycle
17:22:21            during-chaos  456      22.8%   74.4%   Pod rescheduled, CPU drops
17:22:34            during-chaos  490      24.5%   75.0%   Cycles 1-2, moderate churn
17:22:50            during-chaos  387      19.3%   74.1%   Brief dip between kills
17:23:08            during-chaos  508      25.4%   74.0%   Cycles 3-4
17:23:52            during-chaos  358      17.9%   74.7%   Recovery settling
17:24:20            during-chaos  384      19.2%   74.8%   Approaching end of chaos
17:25:04            during-chaos  440      22.0%   74.1%   Stabilizing
17:25:23            post-chaos    637      31.8%   74.3%   POST-CHAOS SPIKE (probes executing)
17:25:33            post-chaos    628      31.4%   73.3%   Settling
```

Key observations:
- CPU spike from ~390m to **642m (+65%)** at 17:22:06 correlates with pod-delete/reschedule churn
- Memory remains stable (74-75%) — pod-delete doesn't cause memory pressure
- Post-chaos spike to **637m** is the ChaosEngine running post-chaos probes
- Recovery to baseline (~440m) takes approximately 30 seconds after the last deletion

### 3.5 Prometheus Metrics During Chaos

Pod ready count remains constant at 16.0 throughout the chaos window. This is because pod-delete targets a single pod which is rapidly rescheduled (~1-2s recovery), and Prometheus scrape interval (~15s) misses the brief unavailability. CPU usage sum stays between 0.56-0.60 cores, consistent with the node-level metrics.

### 3.6 Available Tracing Data Per Run

For any `run_id`, the following can be reconstructed from Neo4j:

| Data | Source Node | Key Fields |
|---|---|---|
| Anomaly window | `AnomalyLabel` | `start_time`, `end_time`, `fault_type`, `severity` |
| Target and impact | `AnomalyLabel` + `AFFECTS` edges | `target_service`, `target_node`, affected `Service` nodes |
| Recovery timeline | `RecoveryCycle` | `deletion_time`, `scheduled_time`, `ready_time`, per-phase ms |
| Node resources | `MetricsSample` | `node_cpu_millicores`, `node_memory_percent`, `pod_total_cpu_millicores` |
| Redis throughput | `MetricsSample` | `redis:write:ops_per_s`, `redis:read:ops_per_s`, `redis:*:latency_ms` |
| Disk I/O | `MetricsSample` | `disk:write:ops_per_s`, `disk:read:ops_per_s` |
| Prometheus | `MetricsSample` | `prom:pod_ready_count:sum`, `prom:cpu_usage:sum`, etc. |
| Probe verdicts | `ProbeResult` | `name`, `verdict`, `mode` (per experiment) |
| Phase summaries | `MetricsPhase` | Aggregated stats per `(metric_type, phase)` |
| Pod state | `PodSnapshot` | `name`, `phase`, `node`, `restart_count` |
| Cascade analysis | `CascadeEvent` | `targetService`, `affectedRoutes`, `cascadeRatio` |
| Container logs | `ContainerLog` | `current_log`, `previous_log` per pod/container |

---

## 4. Tracing Gaps and Improvement Opportunities

### 4.1 P0 — Latency Time-Series Not Collected

**Impact**: Critical. Disables cascade detection, per-route degradation tracking, and the most valuable ML feature.

**Evidence**: 0 of 10,568 MetricsSamples contain latency data. ~52% of all samples are empty shells with only `[timestamp, strategy, phase, seq]` — these are latency prober entries with no route data.

**Root cause**: `probers.py:40` creates `ContinuousLatencyProber(namespace)` without passing `http_routes`. The prober's `_probe_loop()` calls `measure_http_routes(http_routes=None)` which returns `[]` immediately.

**Downstream effects**:
- `cascade.py` walks `latency.timeSeries` to detect degradation windows — finds nothing. Every `CascadeEvent` shows `totalRoutesMonitored: 0, affectedRoutes: []`.
- `timeseries.py:_merge_latency()` finds no data to merge into ML-aligned buckets.
- `MetricsPhase` nodes for type `latency` have no route statistics.

**Fix**: Pass `http_routes` (and optionally `service_routes`) through `create_and_start_probers()` to the `ContinuousLatencyProber` constructor.

### 4.2 P1 — Anomaly Labels Are Declarative, Not Observed

**Impact**: Labels describe what *should* have happened based on the scenario YAML, not what *actually* happened.

**Current behavior**: `anomaly_labels.py:generate_anomaly_labels()` reads the scenario definition and produces one label per experiment with `startTime` = experiment start, `endTime` = experiment end. It doesn't verify:
- Whether the fault was actually injected (could have failed)
- How many pods were actually killed (scenario specifies `PODS_AFFECTED_PERC` but actual count isn't recorded)
- The exact timestamps of each individual deletion (available in `RecoveryCycle` nodes but not cross-referenced)

**Example**: For a run with 7 pod-delete cycles between 17:22:16 and 17:24:02, the anomaly label says "pod-delete from 17:21:40 to 17:25:21" — a 3m41s window that includes pre-chaos probe evaluation and post-chaos probes, not just the actual fault injection period.

**Improvement**: Generate sub-labels from `RecoveryCycle` data. Each deletion-to-ready cycle is a discrete micro-anomaly. This would enable:
- Per-cycle correlation with metric samples
- Accurate "time-in-anomaly" vs "time-in-recovery" vs "time-healthy" classification
- Detection of injection failures (no cycles recorded = fault didn't fire)

### 4.3 P1 — No Causal Linking Between Anomaly and Metric Impact

**Impact**: Analysis requires manual timestamp correlation. ML models must reconstruct causality from raw timestamps.

**Current state**: `AnomalyLabel`, `RecoveryCycle`, and `MetricsSample` are parallel subgraphs under `ChaosRun` with no edges between them. There is no:
- `(RecoveryCycle)-[:CAUSED_SPIKE]->(MetricsSample)` edge
- `recovery_in_progress` flag on `MetricsSample` nodes (only exists in `timeseries.py` aligned export, not in Neo4j)
- `recovery_cycle_id` field on `MetricsSample` nodes

The `timeseries.py:_merge_recovery()` function does add `recovery_in_progress` to aligned CSV/Parquet exports, but this isn't persisted to Neo4j.

**Improvement**: During `_sync_time_series`, check each sample's timestamp against `RecoveryCycle` windows and set `recovery_in_progress` + `cycle_id` on the `MetricsSample` node. Optionally create `DURING_RECOVERY` edges from cycles to samples.

### 4.4 P2 — Recovery Cycle Timestamp Precision

**Impact**: Sub-phase breakdown (scheduling vs startup) is unreliable.

**Evidence**: ~50% of recovery cycles show negative `scheduledToReady_ms` values (e.g., -955ms, -823ms, -816ms). The `scheduled_time` comes from K8s pod events which are rounded to whole seconds, while `ready_time` has millisecond precision from pod condition timestamps.

**Total recovery** (`deletion_time` to `ready_time`) is accurate because both use condition-level timestamps. Only the intermediate `scheduled_time` has the rounding issue.

**Improvement**: Use pod condition `lastTransitionTime` for the `PodScheduled` condition instead of the event timestamp, or interpolate from the deletion and ready timestamps.

### 4.5 P2 — No Explicit Recovery Failure Markers

**Impact**: Incomplete recovery cycles are only detectable by absence of `ready_time`.

**Evidence**: Colocate runs consistently show `completed_cycles: 0, incomplete_cycles: 1`. The pod was deleted but never recovered. However:
- There's no explicit "recovery_failed" flag or timeout marker
- There's no record of *why* it failed (OOMKilled? Unschedulable? Resource pressure?)
- The `RecoveryCycle` node just has `scheduled_time: null, ready_time: null`

**Improvement**: Add `failure_reason` field to `RecoveryCycle`. On recovery timeout, capture the pod's last condition/event (e.g., `Unschedulable`, `OOMKilled`, `ImagePullBackOff`).

### 4.6 P3 — Per-Service Impact Quantification

**Impact**: Blast radius is a static service list, not a measured degradation.

**Current state**: `AnomalyLabel.affectedServices` is computed from the static dependency graph (`_get_affected_services` does a 1-hop lookup in `service_routes`). It says "frontend, checkoutservice, recommendationservice depend on productcatalogservice" but doesn't quantify:
- How much each service degraded (latency increase, error rate)
- When each service first showed degradation
- When each service recovered
- Whether some services were more affected than others

This is the purpose of `cascade.py`, but it can't function without latency data (see P0).

**Improvement**: Once latency collection is fixed, `cascade.py` already has the logic to compute per-route degradation start/end times, peak latency, and degradation rates. These need to be:
1. Actually populated (requires P0 fix)
2. Stored as edges: `(AnomalyLabel)-[:DEGRADED {peak_ms, duration_s, degradation_factor}]->(Service)`

### 4.7 P3 — Prometheus Scrape Interval Too Coarse

**Impact**: Prometheus misses brief pod unavailability.

**Evidence**: `prom:pod_ready_count:sum` stays constant at 16.0 throughout chaos, even during pod-delete cycles with ~1.5s recovery. The default Prometheus scrape interval (15-30s) is longer than the recovery time, so the brief dip is never captured.

**Improvement**: This isn't a code fix but a configuration note. For chaos experiments with sub-15s recovery times, either:
- Reduce Prometheus scrape interval to 5s for target metrics
- Rely on the recovery watcher (K8s watch API, millisecond precision) instead of Prometheus for availability metrics
- Use the continuous resource prober (5s interval) which does capture the CPU spikes

---

## 5. Data Quality Notes

### 5.1 Session 141340 — Broken Load Generation

All strategies in session `141340` show:
- `load_error_rate: 1.0` (100% failure)
- `load_avg_response_ms: ~30,003` (30s timeout)
- `load_rps: ~1.3` (only timeout-limited requests)

The Locust target URL was unreachable, likely due to a port-forward failure after placement changes. Recovery and probe data from this session is still valid (those use direct K8s API), but **all load generation metrics should be excluded**.

### 5.2 Early Sessions — Baseline Instability

Sessions `104513`, `113216`, `114317` have baseline scores of 0. The ChaosEngine infrastructure wasn't stable yet (probes failed even without fault injection). These sessions should be excluded from comparative analysis. They can still be used to study infrastructure warm-up behavior.

### 5.3 Session 132433 — Anomalous Baseline Recovery

Run `run-2026-04-15-133117-6cfdf7` (baseline, session `132433`) shows `mean_recovery_ms: 180,698` (~3 minutes). This is a 125x outlier compared to normal baseline recovery (~1,400ms). Likely a cluster-level issue (node pressure, network partition) during this specific run. Exclude from baseline statistics.

### 5.4 Empty MetricsSamples

52% of all `MetricsSample` nodes contain only metadata (`timestamp`, `strategy`, `phase`, `seq`) with no metric values. These are latency prober entries that recorded nothing due to the missing `http_routes` parameter (see Section 4.1). They can be identified by checking for absence of any key starting with `latency:`, `node_`, `redis:`, `disk:`, or `prom:`.
