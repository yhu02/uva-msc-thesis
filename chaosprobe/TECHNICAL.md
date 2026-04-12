# ChaosProbe Technical Reference

## 1. System Overview

ChaosProbe is a Python framework for automated Kubernetes chaos testing with AI-consumable output. It wraps LitmusChaos to run ChaosEngine experiments via the ChaosCenter GraphQL API, collects real-time pod recovery metrics, and stores all data in a Neo4j graph database for machine-learning feedback loops.

**Core loop**: deploy manifests → run chaos experiments → collect metrics → store in Neo4j → AI reads data, edits manifests, re-runs, compares.

```
ChaosProbe CLI
      │
      ├── cli.py (~263 lines, thin shell)
      │     status, provision, compare, cleanup + command registrations
      │
      ├── commands/  (10 extracted command modules)
      │     run_cmd, init_cmd, delete_cmd, graph_cmd, visualize_cmd,
      │     placement_cmd, cluster_cmd, dashboard_cmd, probe_cmd, shared
      │
      ├── Cluster Manager (provisioner/setup.py, ~1009 lines)
      │     LitmusSetup inherits: _VagrantMixin, _ComponentsMixin,
      │     _ChaosCenterAPIMixin, _ChaosCenterMixin
      │     ├── Vagrant (local dev: multi-node KVM/libvirt cluster)
      │     ├── Kubespray (production bare-metal/cloud)
      │     └── Installs Helm, LitmusChaos, ChaosCenter, RBAC,
      │         metrics-server, Prometheus, Neo4j
      │
      ├── Config Loader (config/loader.py)
      │     ├── Validator (config/validator.py)
      │     └── Topology Parser (config/topology.py)
      │
      ├── Infrastructure Provisioner (provisioner/kubernetes.py)
      │
      ├── Placement Engine
      │     ├── Strategy (placement/strategy.py)
      │     └── Mutator (placement/mutator.py)
      │
      ├── Chaos Runner (chaos/runner.py)
      │     ChaosCenter GraphQL: save → trigger → poll experiments
      │
      ├── Load Generator (loadgen/runner.py)
      │     Locust-based: steady (50u), ramp (100u), spike (200u)
      │
      ├── Metrics Collection
      │     ├── RecoveryWatcher (metrics/recovery.py)
      │     ├── ContinuousProberBase (metrics/base.py)
      │     ├── Latency, Throughput, Resources, Prometheus probers
      │     ├── AnomalyLabels, Cascade, Remediation, TimeSeries
      │     └── MetricsCollector (metrics/collector.py)
      │
      ├── Result Collector (collector/result_collector.py)
      │
      ├── Orchestrator
      │     ├── strategy_runner.py — RunContext + execute_strategy()
      │     ├── run_phases.py — preflight, graph init, result writing
      │     ├── probers.py — create/start/stop continuous probers
      │     └── portforward.py — kubectl port-forward lifecycle
      │
      ├── Output
      │     ├── generator.py — structured JSON output (schema v2.0.0)
      │     ├── comparison.py — before/after run comparison
      │     ├── visualize.py + charts.py — charts, HTML reports
      │     └── ml_export.py — CSV/Parquet ML datasets
      │
      ├── Storage — Neo4j Graph Store (primary)
      │     ├── neo4j_store.py — thin shell (Writer + Reader mixins)
      │     ├── neo4j_writer.py — all write operations
      │     └── neo4j_reader.py — all read operations
      │
      └── Graph Analysis (graph/analysis.py)
            blast radius, topology comparison, colocation impact,
            critical path, strategy summary
```

---

## 2. Module Reference

### 2.1 Configuration (`config/`)

#### loader.py

Loads scenario directories or single YAML files. Auto-classifies resources by `kind`: `ChaosEngine` kinds go to `experiments`, everything else to `manifests`.

| Function | Signature | Purpose |
|---|---|---|
| `load_scenario` | `(scenario_path: str) -> Dict` | Returns `{path, manifests, experiments, namespace, cluster?, probes?}` |
| `_load_yaml_file` | `(filepath: Path) -> Tuple[List, List]` | Parses multi-document YAML, classifies by kind |
| `_load_yaml_directory` | `(dirpath: Path) -> Tuple[List, List]` | Loads all .yaml/.yml from directory |
| `_detect_namespace` | `(experiments: List) -> str` | Extracts namespace from ChaosEngine appinfo (default: `"default"`) |
| `_load_cluster_config` | `(dirpath: Path) -> Optional[Dict]` | Loads `cluster.yaml` if present |
| `_detect_rust_probes` | `(dirpath: Path) -> List` | Discovers Rust cmdProbe sources in `probes/` subdirectory |

**Constant**: `CHAOS_KINDS = {"ChaosEngine"}`

#### topology.py

Extracts service-to-service dependencies from Kubernetes Deployment manifests by parsing environment variables (`*_SERVICE_ADDR`, `*_ADDR`, `*_SERVICE_HOST`).

| Function | Signature | Purpose |
|---|---|---|
| `parse_topology_from_scenario` | `(scenario: Dict) -> List[ServiceRoute]` | Extracts routes from a loaded scenario dict |
| `parse_topology_from_directory` | `(deploy_dir: str) -> List[ServiceRoute]` | Loads all YAML files and extracts routes |
| `parse_topology_from_manifests` | `(manifests: List[Dict]) -> List[ServiceRoute]` | Extracts routes from parsed manifest dicts |

**Type**: `ServiceRoute = Tuple[str, str, str, str, str]` — `(source_service, target_service, target_host, protocol, description)`

#### validator.py

Validates scenarios for structural correctness before execution. Validates all LitmusChaos probe types.

| Function | Purpose |
|---|---|
| `validate_scenario(scenario)` | Validates entire scenario. Raises `ValidationError` with aggregated errors |
| `_validate_chaos_engine(spec, filepath)` | Checks apiVersion, kind, experiments, applabel, chaosServiceAccount, probes |
| `_validate_probe(probe, filepath, exp_name)` | Validates probe name, type, mode, runProperties, type-specific inputs |
| `_validate_manifest(spec, filepath)` | Checks apiVersion, kind, metadata.name |
| `_validate_cluster_config(cluster)` | Checks provider (vagrant/kubespray), workers config |

**Supported probe types**: `httpProbe`, `cmdProbe`, `k8sProbe`, `promProbe`
**Supported probe modes**: `SOT`, `EOT`, `Edge`, `Continuous`, `OnChaos`

---

### 2.2 Chaos Execution (`chaos/`)

#### runner.py

Runs experiments via the ChaosCenter GraphQL API. Each ChaosEngine spec is wrapped in an Argo Workflow, saved via `saveChaosExperiment`, triggered via `runChaosExperiment`, and polled via `getExperimentRun`.

**Class: `ChaosRunner(namespace, timeout=300, chaoscenter=None)`**

| Method | Purpose |
|---|---|
| `run_experiments(experiments)` | Saves, triggers, and polls experiments via ChaosCenter |
| `get_executed_experiments()` | Returns metadata for all executed experiments |

**`chaoscenter` dict** (keys: `token`, `project_id`, `infra_id`, `gql_url`): Required for ChaosCenter API integration.

**Terminal phases**: `Completed`, `Completed_With_Error`, `Completed_With_Probe_Failure`, `Stopped`, `Error`, `Timeout`, `Terminated`, `Skipped`.

---

### 2.3 Result Collection (`collector/`)

#### result_collector.py

Collects ChaosResult CRDs and calculates resilience metrics. Supports all LitmusChaos probe types with type-aware parsing.

**Class: `ResultCollector(namespace)`**

| Method | Purpose |
|---|---|
| `collect(executed_experiments)` | Collects results for all executed experiments |

**Module function**: `calculate_resilience_score(results, weights=None) -> float` — weighted average of probe success percentages (0-100).

**Probe type normalisation**: `HTTPProbe` → `httpProbe`, `CmdProbe` → `cmdProbe`, `K8sProbe` → `k8sProbe`, `PromProbe` → `promProbe`.

---

### 2.4 Metrics Collection (`metrics/`)

#### base.py — ContinuousProberBase

Abstract base for all continuous probers. Manages background thread lifecycle, phase tracking (PreChaos/DuringChaos/PostChaos), and aggregation.

Subclasses: `ContinuousLatencyProber`, `ContinuousRedisProber`, `ContinuousDiskProber`, `ContinuousResourceProber`, `ContinuousPrometheusProber`.

#### recovery.py — RecoveryWatcher

**Class: `RecoveryWatcher(namespace, deployment_name)`**

Background thread using the Kubernetes watch API to observe pod lifecycle events in real-time. Records deletion and ready timestamps as they happen.

| Method | Purpose |
|---|---|
| `start()` | Snapshots current pods, starts background watch thread |
| `stop()` | Stops watch, finalizes any pending recovery cycle |
| `result()` | Returns structured recovery data with cycles and summary |

**Recovery cycle**: DELETED → PodScheduled → Ready. Records `deletionToScheduled_ms`, `scheduledToReady_ms`, `totalRecovery_ms`.

**Summary statistics**: count, completedCycles, mean, median, min, max, p95 (all in ms).

#### collector.py — MetricsCollector

**Class: `MetricsCollector(namespace)`**

Orchestrates post-experiment data collection and merges with pre-collected watcher data.

Output includes: `deploymentName`, `timeWindow`, `recovery`, `podStatus`, `eventTimeline`, `nodeInfo`, plus continuous prober data (latency, throughput, resources, prometheus).

---

### 2.5 Placement Engine (`placement/`)

#### strategy.py

**Enum: `PlacementStrategy`** — `colocate`, `spread`, `random`, `antagonistic`

| Strategy | Behavior |
|---|---|
| `colocate` | All deployments pinned to a single node (max resource contention) |
| `spread` | Round-robin across all schedulable nodes (min contention) |
| `random` | Random assignment per deployment (reproducible with seed) |
| `antagonistic` | Top-N resource-heavy deployments on one node, rest distributed |

**Dataclasses**: `NodeInfo`, `DeploymentInfo`, `NodeAssignment`

**Entry point**: `compute_assignments(strategy, deployments, nodes, target_node=None, seed=None) -> NodeAssignment`

#### mutator.py

**Class: `PlacementMutator(namespace)`**

| Method | Purpose |
|---|---|
| `get_nodes()` | Queries all cluster nodes with resource info |
| `get_deployments()` | Lists deployments with resource requests |
| `apply_strategy(strategy, target_node=None, seed=None)` | Computes + applies strategy, waits for rollout |
| `clear_placement()` | Removes nodeSelector from all managed deployments |
| `get_current_placement()` | Returns per-deployment placement state |

**Mechanism**: Patches `spec.template.spec.nodeSelector` with `kubernetes.io/hostname`. Tracks via `chaosprobe.io/placement-strategy` annotation.

---

### 2.6 Output (`output/`)

#### generator.py

**Class: `OutputGenerator(scenario, results, metrics=None, placement=None, service_routes=None)`**

| Method | Purpose |
|---|---|
| `generate()` | Returns full output dict: scenario, infrastructure, experiments, summary, metrics |

**Schema version**: `2.0.0`. Top-level keys: `schemaVersion`, `runId`, `timestamp`, `scenario`, `infrastructure`, `experiments`, `summary`, `metrics`, `loadGeneration`, `anomalyLabels`, `cascadeTimeline`.

#### comparison.py

**Function**: `compare_runs(baseline, after_fix, improvement_criteria=None) -> Dict`

Compares two experiment runs and evaluates fix effectiveness.

- `fixEffective = True` if verdict FAIL→PASS, or score change ≥ 20, or all criteria met
- Confidence: base 0.5, +0.25 if verdict changed, +min(0.15, score_change/100), +0.10 if all experiments improved

#### visualize.py + charts.py

`visualize.py` orchestrates chart generation and HTML summary. `charts.py` contains all matplotlib chart functions.

| Function (visualize.py) | Purpose |
|---|---|
| `generate_from_summary(summary_path, output_dir)` | Charts from legacy summary.json |
| `generate_from_dict(summary, output_dir)` | Charts from in-memory dict |

| Function (charts.py) | Purpose |
|---|---|
| `chart_resilience_scores(...)` | Resilience score bar chart per strategy |
| `chart_recovery_times(...)` | Mean/p95 recovery time comparison |
| `chart_latency_by_strategy(...)` | Inter-service latency comparison |
| `chart_throughput_by_strategy(...)` | Throughput comparison |
| `chart_resource_utilization(...)` | Resource utilization comparison |
| `chart_prometheus_by_phase(...)` | Prometheus metrics by experiment phase |

#### ml_export.py

| Function | Purpose |
|---|---|
| `export_run_to_rows(run_data)` | Convert run JSON to aligned time-series rows |
| `export_from_neo4j(store, ...)` | Export ML-ready rows directly from Neo4j |

Produces CSV (default) or Parquet (`--format parquet`, requires `pyarrow`).

---

### 2.7 Load Generation (`loadgen/`)

#### runner.py

Locust-based load generator with preset profiles and CSV stats parsing.

| Profile | Users | Spawn Rate | Duration |
|---|---|---|---|
| `steady` | 50 | 10/s | 120s |
| `ramp` | 100 | 5/s | 180s |
| `spike` | 200 | 50/s | 90s |

**Class: `LocustRunner(target_url, locustfile=None)`** — supports context manager protocol.

| Method | Purpose |
|---|---|
| `start(profile)` | Start headless Locust with the given profile |
| `stop()` | Terminate the Locust process |
| `wait()` | Wait for Locust to complete |
| `collect_stats()` | Parse CSV output into `LoadStats` |
| `cleanup()` | Remove temporary directories |

**Default locustfile**: Simulates web browsing (index, browse, cart, checkout) with `FrontendUser` class.

---

### 2.8 Storage (`storage/`)

Neo4j is the **sole persistent store**. No SQLite.

#### neo4j_store.py

**Class: `Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="neo4j")`**

Thin shell composing `Neo4jWriterMixin` and `Neo4jReaderMixin`. Supports context manager protocol.

#### neo4j_writer.py (~872 lines)

All write operations: topology sync, run persistence, metrics samples, time-series data, anomaly labels, cascade events, pod snapshots.

#### neo4j_reader.py (~876 lines)

All read operations: run reconstruction (`get_run_output`), session queries, blast radius, strategy summaries, visualization data aggregation.

**Key nodes**: `ChaosRun`, `MetricsSample`, `AnomalyLabel`, `CascadeEvent`, `ExperimentResult`, `MetricsPhase`, `PodSnapshot`, `RecoveryCycle`.

**Session grouping**: via `session_id` property on `ChaosRun` nodes.

---

### 2.9 Infrastructure (`provisioner/`)

#### kubernetes.py

**Class: `KubernetesProvisioner(namespace)`**

Applies standard K8s manifests. Supports: Deployment, Service, ConfigMap, NetworkPolicy, PodDisruptionBudget, Secret, DaemonSet, StatefulSet, and generic kinds.

| Method | Purpose |
|---|---|
| `provision(manifests)` | Ensures namespace, applies all manifests, waits for readiness |
| `cleanup()` | Deletes all applied resources in reverse order |
| `cleanup_namespace()` | Deletes entire namespace |

#### setup.py (~1,009 lines)

**Class: `LitmusSetup`** — inherits `_VagrantMixin`, `_ComponentsMixin`, `_ChaosCenterAPIMixin`, `_ChaosCenterMixin`.

| Capability | Key Methods |
|---|---|
| Prerequisites | `check_prerequisites()`, `validate_cluster()`, `get_cluster_info()` |
| LitmusChaos | `install_litmus()`, `setup_rbac()`, `install_experiment()` |
| ChaosCenter | `install_chaoscenter()`, `chaoscenter_save_experiment()`, `chaoscenter_run_experiment()` |
| Components | `is_metrics_server_installed()`, `is_prometheus_installed()`, `is_neo4j_installed()` |
| Vagrant | `create_vagrantfile()`, `vagrant_up()`, `vagrant_deploy_cluster()`, `vagrant_destroy()` |
| Kubespray | `deploy_cluster()`, `generate_inventory()`, `get_kubeconfig()` |

**Mixins** in separate files:
- `provisioner/vagrant.py` — `_VagrantMixin` (~542 lines)
- `provisioner/components.py` — `_ComponentsMixin` (~433 lines)
- `provisioner/chaoscenter.py` — `_ChaosCenterMixin` (~888 lines)

**Defaults**: Vagrant box `generic/ubuntu2204`, 2 CPUs, 4096MB RAM, Kubespray v2.24.0.

---

### 2.10 Orchestrator (`orchestrator/`)

#### strategy_runner.py (~433 lines)

**Dataclass: `RunContext`** — carries all state for a run: namespace, timeout, seed, settle_time, iterations, measurement flags, Neo4j credentials, shared scenario, service routes, etc.

**Function**: `execute_strategy(ctx, strategy_name, idx, total)` — executes one placement strategy: apply placement → settle → run iterations → collect results → clear placement.

#### run_phases.py (~581 lines)

Pre-flight checks, graph store initialization, result writing, iteration aggregation, stale resource cleanup.

#### probers.py (~209 lines)

`create_and_start_probers()`, `stop_and_collect_probers()` — manages continuous prober lifecycle in parallel.

#### portforward.py (~120 lines)

Module-level kubectl port-forward lifecycle management. Start/stop/ensure port-forwards for Neo4j, Prometheus, frontend.

---

### 2.11 Graph Analysis (`graph/`)

#### analysis.py (~120 lines)

High-level Neo4j graph queries:

| Function | Purpose |
|---|---|
| `blast_radius_report(store, service)` | Upstream services affected by a failure |
| `topology_comparison(store, run_ids)` | Compare placement topologies across runs |
| `colocation_impact(store, run_ids)` | Resource contention from co-location |
| `critical_path_analysis(store)` | Longest dependency chain |
| `strategy_summary(store, run_ids)` | Outcomes grouped by strategy |

---

## 3. CLI Commands

### Core

| Command | Purpose |
|---|---|
| `chaosprobe status [--json]` | Check prerequisites and cluster connectivity |
| `chaosprobe init [-n namespace] [--skip-litmus] [--skip-dashboard]` | Install all infrastructure (LitmusChaos, ChaosCenter, Prometheus, Neo4j, metrics-server) |
| `chaosprobe run [-n namespace]` | Run placement experiment matrix |
| `chaosprobe provision <scenario>` | Deploy manifests only |
| `chaosprobe compare <baseline> <afterfix> [--neo4j-uri]` | Compare before/after runs (Neo4j run IDs or JSON files) |
| `chaosprobe cleanup <namespace> [--all]` | Remove provisioned resources |
| `chaosprobe delete [-n namespace]` | Delete ALL ChaosProbe infrastructure (ChaosCenter, Prometheus, Neo4j, etc.) |

### Run Options

| Option | Default | Purpose |
|---|---|---|
| `-n, --namespace` | from experiment YAML | Target namespace |
| `-o, --output-dir` | `results` | Base results directory (timestamped subdir created) |
| `-s, --strategies` | `baseline,colocate,spread,antagonistic,random` | Comma-separated strategies |
| `-i, --iterations` | 1 | Iterations per strategy |
| `-e, --experiment` | `scenarios/online-boutique/placement-experiment.yaml` | Experiment YAML file |
| `-t, --timeout` | 300 | Timeout per experiment (seconds) |
| `--seed` | 42 | Random strategy seed |
| `--settle-time` | 30 | Wait after placement before experiment |
| `--provision` | off | Auto-provision cluster from scenario config |
| `--load-profile` | `steady` | Locust profile: steady/ramp/spike |
| `--locustfile` | built-in | Custom locustfile path |
| `--target-url` | auto port-forward | URL for Locust load generation |
| `--visualize/--no-visualize` | on | Generate charts after run |
| `--measure-latency/--no-measure-latency` | on | Inter-service latency |
| `--measure-redis/--no-measure-redis` | on | Redis throughput |
| `--measure-disk/--no-measure-disk` | on | Disk I/O throughput |
| `--measure-resources/--no-measure-resources` | on | Node/pod resource utilization |
| `--measure-prometheus/--no-measure-prometheus` | on | Prometheus cluster metrics |
| `--collect-logs/--no-collect-logs` | on | Container logs from target deployment |
| `--prometheus-url` | auto-discovered | Prometheus URL(s); repeat for multiple |
| `--baseline-duration` | 0 | Seconds of steady-state collection before chaos |
| `--neo4j-uri` | `bolt://localhost:7687` | Neo4j URI (env: `NEO4J_URI`) |
| `--neo4j-user` | `neo4j` | Neo4j username (env: `NEO4J_USER`) |
| `--neo4j-password` | `chaosprobe` | Neo4j password (env: `NEO4J_PASSWORD`) |

### Placement

| Command | Purpose |
|---|---|
| `chaosprobe placement apply <strategy> -n <ns>` | Apply strategy (colocate/spread/random/antagonistic) |
| `chaosprobe placement show -n <ns>` | Display current pod placement |
| `chaosprobe placement nodes` | List cluster nodes with resources |
| `chaosprobe placement clear -n <ns>` | Remove all placement constraints |

### Graph (Neo4j)

| Command | Purpose |
|---|---|
| `chaosprobe graph status` | Check Neo4j connectivity, show node counts |
| `chaosprobe graph sessions` | List experiment sessions |
| `chaosprobe graph blast-radius <service> [--max-hops N]` | Upstream dependents affected by failure |
| `chaosprobe graph topology --run-id <id>` | Placement topology for a run |
| `chaosprobe graph details <run-id> [--json]` | Full stored data for a run |
| `chaosprobe graph compare --run-ids <id1,id2,...>` | Compare strategies across runs |

All graph commands accept `--neo4j-uri`, `--neo4j-user`, `--neo4j-password`.

### Dashboard (ChaosCenter)

| Command | Purpose |
|---|---|
| `chaosprobe dashboard install` | Install ChaosCenter on the cluster |
| `chaosprobe dashboard status` | Show pod health and dashboard URL |
| `chaosprobe dashboard open` | Open dashboard in browser |
| `chaosprobe dashboard connect -n <ns>` | Connect namespace to ChaosCenter |
| `chaosprobe dashboard credentials` | Show ChaosCenter login credentials |

### Visualize

| Command | Purpose |
|---|---|
| `chaosprobe visualize --neo4j-uri <uri> [--session <id>] -o <dir>` | Charts from Neo4j |
| `chaosprobe visualize --summary <file> -o <dir>` | Charts from legacy summary.json |

### ML Export

| Command | Purpose |
|---|---|
| `chaosprobe ml-export --neo4j-uri <uri> -o <file>` | Export aligned time-series from Neo4j |

Options: `--format csv|parquet`, `--strategy <name>`. Parquet requires `pyarrow`.

### Probe (Rust cmdProbes)

| Command | Purpose |
|---|---|
| `chaosprobe probe init --scenario <path>` | Scaffold a new Rust cmdProbe |
| `chaosprobe probe build --scenario <path>` | Build Rust probe binaries |
| `chaosprobe probe list --scenario <path>` | List discovered probes |

### Cluster Management

| Command | Purpose |
|---|---|
| `chaosprobe cluster vagrant init` | Generate Vagrantfile for multi-node cluster |
| `chaosprobe cluster vagrant setup` | Setup libvirt provider (WSL2/Linux) |
| `chaosprobe cluster vagrant up` | Start VMs with libvirt |
| `chaosprobe cluster vagrant deploy` | Deploy K8s via Kubespray on VMs |
| `chaosprobe cluster vagrant kubeconfig` | Fetch kubeconfig from control plane |
| `chaosprobe cluster vagrant status` | Check VM and cluster health |
| `chaosprobe cluster vagrant ssh <vm>` | SSH into a VM |
| `chaosprobe cluster vagrant destroy` | Tear down VMs |
| `chaosprobe cluster create --hosts-file` | Production cluster via Kubespray |
| `chaosprobe cluster kubeconfig --host <ip>` | Fetch kubeconfig from remote host |
| `chaosprobe cluster destroy --inventory <path>` | Destroy Kubespray cluster |

---

## 4. Data Flow: `run` Command

```
1. Load experiment YAML (placement-experiment.yaml)
2. Parse service topology from scenario manifests (config/topology.py)
3. Auto-deploy application manifests from deploy/ subdirectory
4. Extract target deployment from ChaosEngine appinfo
5. Auto-build Rust cmdProbes if probes/ directory exists
6. ChaosCenter authentication (get token, project, infra)

For each strategy in [baseline, colocate, spread, antagonistic, random]:
    7. Apply placement via PlacementMutator
    8. Wait settle-time (30s default)

    For each iteration (1..N):
        9.  Start RecoveryWatcher(namespace, target_deployment)
        10. Start ContinuousProbers (latency, redis, disk, resources, prometheus)
        11. Start LocustRunner with load profile
        12. ChaosRunner.run_experiments() — save in ChaosCenter,
            trigger via GraphQL, poll getExperimentRun until completion
        13. Stop LocustRunner, collect LoadStats
        14. Stop RecoveryWatcher and ContinuousProbers
        15. ResultCollector.collect() — ChaosResult CRDs
        16. MetricsCollector.collect() — merge all prober data
        17. OutputGenerator.generate() — build output_data dict
        18. Neo4jStore.sync_run(output_data) — persist to graph

    19. Clear placement constraints
    20. Wait for rollout

21. Build comparison table + remediation log
22. Close Neo4jStore
23. Generate visualization charts (on by default)
```

---

## 5. Output Schema (v2.0.0)

### Experiment Result

```json
{
  "schemaVersion": "2.0.0",
  "runId": "run-2026-02-27-141052-abc123",
  "timestamp": "2026-02-27T14:10:52+00:00",
  "scenario": {
    "directory": "scenarios/online-boutique",
    "manifests": [{"file": "...", "content": {...}}],
    "experiments": [{"file": "...", "content": {...}}]
  },
  "infrastructure": {"namespace": "online-boutique"},
  "experiments": [{
    "name": "pod-delete",
    "engineName": "placement-pod-delete-colocate-a1b2c3",
    "result": {
      "phase": "Completed_With_Probe_Failure",
      "verdict": "Fail",
      "probeSuccessPercentage": 50.0,
      "failStep": ""
    },
    "probes": [{
      "name": "frontend-availability",
      "type": "httpProbe",
      "mode": "Continuous",
      "status": {"verdict": "Passed", "description": "..."}
    }]
  }],
  "summary": {
    "totalExperiments": 1,
    "passed": 0,
    "failed": 1,
    "resilienceScore": 50.0,
    "overallVerdict": "FAIL"
  },
  "metrics": {
    "deploymentName": "checkoutservice",
    "timeWindow": {"start": "...", "end": "...", "duration_s": 167.3},
    "recovery": {
      "recoveryEvents": [{
        "deletionTime": "...",
        "scheduledTime": "...",
        "readyTime": "...",
        "deletionToScheduled_ms": 120,
        "scheduledToReady_ms": 880,
        "totalRecovery_ms": 1000
      }],
      "summary": {
        "count": 8, "completedCycles": 8,
        "meanRecovery_ms": 1521.0, "medianRecovery_ms": 1401.0,
        "minRecovery_ms": 790, "maxRecovery_ms": 2834,
        "p95Recovery_ms": 2834.0
      }
    }
  }
}
```

---

## 6. Resilience Scoring

**Resilience score** = weighted average of probe success percentages across experiments.

```
score = sum(probeSuccessPercentage[i] * weight[i]) / sum(weight[i])
```

Default weight: 1.0 per experiment. Range: 0-100.

**Overall verdict**: `PASS` if all experiments pass, `FAIL` otherwise.

**Comparison confidence**:
```
confidence = 0.50 (base)
  + 0.25 (if verdict changed)
  + min(0.15, score_change / 100)
  + 0.10 (if all experiments improved)
```

---

## 7. Placement Experiment Design

### Hypothesis

Pod scheduling recovery time varies with placement strategy due to differences in resource contention on the target node.

### Experiment: pod-delete on checkoutservice

- **TOTAL_CHAOS_DURATION**: 120s
- **CHAOS_INTERVAL**: 10s (12 deletions per run)
- **FORCE**: true (immediate termination)
- **PODS_AFFECTED_PERC**: 100%

### Probes

| Probe | Type | Purpose |
|---|---|---|
| `frontend-availability` | httpProbe (Continuous, 1s) | Frontend returns HTTP 200 |
| `checkout-pod-ready` | k8sProbe (Continuous, 1s) | Running checkoutservice pod exists |

### Strategy Configurations

| Strategy | Assignment | Expected Contention |
|---|---|---|
| baseline | Default scheduler | Low (scheduler distributes) |
| colocate | All 12 services on one node | Maximum |
| spread | 4 per node (round-robin) | Minimum |
| antagonistic | 6 heavy services + target on one node | High |
| random | Random per-deployment (seed=42) | Variable |

---

## 8. File Organization

```
chaosprobe/
  chaosprobe/
    __init__.py              # version 0.1.0
    cli.py                   # CLI entry point (~263 lines)
    k8s.py                   # Singleton k8s config loader
    commands/
      shared.py              # Neo4j option decorators, shared helpers
      run_cmd.py             # run command (~370 lines)
      init_cmd.py            # init command (~224 lines)
      delete_cmd.py          # delete command (~154 lines)
      graph_cmd.py           # graph subcommands
      visualize_cmd.py       # visualize + ml-export commands
      placement_cmd.py       # placement subcommands
      cluster_cmd.py         # cluster + vagrant subcommands
      dashboard_cmd.py       # ChaosCenter dashboard subcommands
      probe_cmd.py           # Rust cmdProbe subcommands
    config/
      loader.py              # Scenario loading
      topology.py            # Service dependency extraction
      validator.py           # Validation
    chaos/
      runner.py              # ChaosCenter GraphQL experiment execution
      manifest.py            # Argo Workflow manifest generation
    collector/
      result_collector.py    # ChaosResult collection
    loadgen/
      runner.py              # Locust load generation
    metrics/
      base.py                # ContinuousProberBase
      recovery.py            # Real-time pod watch
      collector.py           # Metrics aggregation
      latency.py             # Inter-service latency prober
      throughput.py          # Redis/disk throughput probers
      resources.py           # Resource usage prober
      prometheus.py          # Prometheus metrics prober
      anomaly_labels.py      # Ground-truth ML labels
      cascade.py             # Fault propagation tracking
      timeseries.py          # Time-series alignment
      remediation.py         # Remediation action logs
    orchestrator/
      strategy_runner.py     # RunContext + execute_strategy()
      run_phases.py          # Preflight, graph init, result writing
      probers.py             # Continuous prober lifecycle
      portforward.py         # Port-forward management
      preflight.py           # Pre-flight checks
    output/
      generator.py           # Structured output generation
      comparison.py          # Run comparison
      visualize.py           # Chart orchestrator + HTML summary
      charts.py              # All matplotlib chart functions
      ml_export.py           # ML-ready dataset export
    placement/
      strategy.py            # Placement strategies + dataclasses
      mutator.py             # K8s patch operations
    provisioner/
      kubernetes.py          # Manifest application
      setup.py               # LitmusSetup main class (~1009 lines)
      vagrant.py             # _VagrantMixin (~542 lines)
      components.py          # _ComponentsMixin (~433 lines)
      chaoscenter.py         # _ChaosCenterMixin (~888 lines)
    probes/
      builder.py             # RustProbeBuilder
      templates.py           # Cargo.toml, main.rs, Dockerfile templates
    storage/
      neo4j_store.py         # Neo4j graph store (~112 lines)
      neo4j_writer.py        # Write operations (~872 lines)
      neo4j_reader.py        # Read operations (~876 lines)
    graph/
      analysis.py            # High-level graph analysis functions
  scenarios/
    online-boutique/
      deploy/                # 12 microservice manifests
      placement-experiment.yaml
      contention-*/          # CPU, memory, IO, network, Redis variants
    examples/
      nginx-pod-delete/      # Simple example scenario
      nginx-all-probes/
      nginx-pod-delete-strict/
      nginx-rust-probe/
  tests/
    conftest.py
    test_config.py           test_placement.py
    test_output.py           test_loadgen.py
    test_neo4j_store.py      test_ml_export.py
    test_latency.py          test_throughput.py
    test_resources.py        test_prometheus.py
    test_collector.py        test_timeseries.py
    test_cascade.py          test_anomaly_labels.py
    test_remediation.py      test_topology.py
    test_visualize.py        test_recovery.py
    test_graph_analysis.py   test_chaoscenter.py
    test_probe_builder.py
  pyproject.toml
  README.md
  TECHNICAL.md
```

---

## 9. Dependencies

### Runtime
- `kubernetes` (>=28.0.0) — Official Python K8s client
- `click` (>=8.0.0) — CLI framework
- `pyyaml` (>=6.0) — YAML parsing
- `locust` (>=2.20.0) — Load generation
- `matplotlib` (>=3.7.0) — Chart generation

### Optional Extras
- `neo4j` (>=5.0.0) — Neo4j driver (`pip install chaosprobe[graph]`)
- `pyarrow` (>=12.0.0) — Parquet export (`pip install chaosprobe[parquet]`)

### Development
- `pytest`, `pytest-cov` — Testing
- `black`, `ruff` — Formatting/linting
- `mypy` — Type checking

### External Services
- Kubernetes cluster (any version with CRD support)
- LitmusChaos + ChaosCenter (auto-installed via `chaosprobe init`)
- Neo4j (auto-installed via `chaosprobe init`)
- Prometheus (auto-installed via `chaosprobe init`)
- Vagrant + libvirt/KVM (local dev only)
