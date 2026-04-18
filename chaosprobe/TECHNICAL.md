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
      ├── Cluster Manager (provisioner/setup.py, ~1000 lines)
      │     LitmusSetup inherits: _VagrantMixin, _ComponentsMixin,
      │     _ChaosCenterAPIMixin, _ChaosCenterMixin
      │     ├── Vagrant (local dev: multi-node KVM/libvirt cluster)
      │     ├── Kubespray (production bare-metal/cloud)
      │     ├── Installs Helm, LitmusChaos, ChaosCenter, RBAC,
      │     │   metrics-server, Prometheus, Neo4j
      │     └── ChaosCenter API/GraphQL (chaoscenter_api.py)
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

**Helpers**: `find_ready_pod()` finds a ready pod by `app=` label. `find_probe_pod()` auto-discovers any pod with a shell (and optionally python3) in the namespace — no hardcoded service preferences. `pod_has_shell()` verifies shell access via `kubectl exec`.

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

**Enum: `PlacementStrategy`** — `colocate`, `spread`, `random`, `adversarial`, `best-fit`, `dependency-aware`

| Strategy | Behavior |
|---|---|
| `colocate` | All deployments pinned to a single node (max resource contention) |
| `spread` | Round-robin across all schedulable nodes (min contention) |
| `random` | Random assignment per deployment (reproducible with seed) |
| `adversarial` | Top-N resource-heavy deployments on one node (worst-fit) |
| `best-fit` | Best-fit decreasing bin-packing (Borg-style; concentrates load on fewest nodes) |
| `dependency-aware` | BFS partition over the service-dependency graph (co-locates communicating services; root selected by lowest in-degree) |

**Dataclasses**: `NodeInfo`, `DeploymentInfo`, `NodeAssignment`

**Entry point**: `compute_assignments(strategy, deployments, nodes, target_node=None, seed=None, dependencies=None) -> NodeAssignment`

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

ML-ready dataset export. See **Section 11 → Programmatic Export** for full API/CLI docs and column definitions.

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

All read operations: run reconstruction (`get_run_output`), session queries, blast radius, strategy summaries, visualization data aggregation. See **Section 10** for the full graph schema and **Section 11** for the Cypher query cookbook.

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
- `provisioner/vagrant.py` — `_VagrantMixin` (~591 lines)
- `provisioner/components.py` — `_ComponentsMixin` (~439 lines)
- `provisioner/chaoscenter.py` — `_ChaosCenterMixin` (~520 lines)
- `provisioner/chaoscenter_api.py` — `_ChaosCenterAPIMixin` (~801 lines)

**Defaults**: Vagrant box `generic/ubuntu2204`, 2 CPUs, 4096MB RAM, Kubespray v2.24.0.

---

### 2.10 Orchestrator (`orchestrator/`)

#### strategy_runner.py (~617 lines)

**Dataclass: `RunContext`** — carries all state for a run: namespace, timeout, seed, settle_time, iterations, measurement flags, Neo4j credentials, shared scenario, service routes, `load_service` (entry-point service name derived from scenario), etc.

**Function**: `execute_strategy(ctx, strategy_name, idx, total)` — executes one placement strategy: apply placement → settle → run iterations → collect results → clear placement.

#### run_phases.py (~669 lines)

Pre-flight checks, graph store initialization, result writing, iteration aggregation, stale resource cleanup. `_setup_load_target()` accepts a `load_service` parameter (derived from the scenario's httpProbe URLs) for port-forwarding to the application entry-point.

#### probers.py (~209 lines)

`create_and_start_probers()`, `stop_and_collect_probers()` — manages continuous prober lifecycle in parallel. Passes `exclude_services=[target_deployment]` to the disk prober so it avoids benchmarking on the pod being deleted by chaos.

#### preflight.py

| Function | Purpose |
|---|---|
| `extract_target_deployment(scenario)` | Extracts target deployment from ChaosEngine appinfo. Raises `ValueError` if not found. |
| `extract_load_service(scenario)` | Extracts the load-target service name from the scenario's httpProbe URLs. |
| `extract_experiment_types(scenario)` | Lists LitmusChaos experiment types referenced in the scenario. |
| `wait_for_healthy_deployments(namespace)` | Blocks until all deployments in the namespace are fully ready. |
| `check_pods_ready(namespace, label)` | Checks that at least one matching pod is Running and Ready. |

#### portforward.py (~120 lines)

Module-level kubectl port-forward lifecycle management. Start/stop/ensure port-forwards for Neo4j, Prometheus, and the application entry-point service.

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

See **Section 11 → Cypher Query Cookbook** for the underlying Cypher queries.

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
| `-s, --strategies` | `baseline,default,colocate,spread,adversarial,random,best-fit,dependency-aware` | Comma-separated strategies |
| `-i, --iterations` | 1 | Iterations per strategy |
| `-e, --experiment` | `scenarios/online-boutique/placement-experiment.yaml` | Experiment YAML file |
| `-t, --timeout` | 300 | Timeout per experiment (seconds) |
| `--seed` | 42 | Random strategy seed |
| `--settle-time` | 30 | Wait after placement before experiment |
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
| `chaosprobe placement apply <strategy> -n <ns>` | Apply strategy (colocate/spread/random/adversarial/best-fit/dependency-aware) |
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

Options: `--format csv|parquet`, `--strategy <name>`. Parquet requires `pyarrow`. See **Section 11 → Programmatic Export** for full usage examples and output column definitions.

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

See **Section 11 → End-to-End Example Flow** for a detailed walkthrough with diagrams. In summary:

1. Load experiment YAML, parse topology, deploy manifests, authenticate with ChaosCenter
2. For each strategy: apply placement → settle → run iterations → clear placement
3. Per iteration: start watchers/probers/load → run chaos → stop all → collect results → sync to Neo4j
4. Write per-strategy JSON + summary, generate charts

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
    "deploymentName": "productcatalogservice",
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

Microservice resilience under chaos varies with pod placement strategy due to differences in resource contention on co-located nodes. Specifically, placement affects:

1. **Pod recovery time** — deletion-to-ready latency under scheduling pressure
2. **Inter-service latency** — HTTP response times across dependent services during and after faults
3. **I/O throughput** — Redis read/write operations and disk throughput on contended nodes
4. **Resource utilisation** — CPU and memory pressure on nodes hosting co-located workloads
5. **Cascade propagation** — how faults in one service degrade upstream consumers

### Experiment: pod-delete on productcatalogservice

- **TOTAL_CHAOS_DURATION**: 120s
- **CHAOS_INTERVAL**: 5s (24 deletions per run)
- **FORCE**: true (immediate termination)
- **PODS_AFFECTED_PERC**: 100%

### Probes

6 probes with a spread of sensitivities for granular scoring (0/17/33/50/67/83/100%):

| Probe | Type | Mode | Tolerance | Purpose |
|---|---|---|---|---|
| `frontend-product-strict` | httpProbe | Continuous (2s) | 3s timeout, 1 retry (≈6s) | Strict: confirms disruption via product page |
| `frontend-homepage-strict` | httpProbe | Continuous (2s) | 3s timeout, 1 retry (≈6s) | Strict: confirms disruption via homepage |
| `frontend-homepage-moderate` | httpProbe | Continuous (3s) | 3s timeout, 2 retries (≈9s) | Moderate: passes only with fast (<3s) recovery |
| `frontend-cart` | httpProbe | Continuous (4s) | 5s timeout, 2 retries (≈15s) | Moderate control: detects node contention (cart is independent) |
| `frontend-homepage-edge` | httpProbe | Edge (5s) | 15s timeout, 5 retries | Edge: validates eventual recovery |
| `frontend-healthz` | httpProbe | Continuous (4s) | 5s timeout, 2 retries (≈15s) | Control: detects node-level resource pressure |

### Strategy Configurations

| Strategy | Assignment | Expected Contention |
|---|---|---|
| baseline | Default scheduler (trivial fault) | None (control) |
| default | Default scheduler (full chaos) | Low (scheduler distributes) |
| colocate | All pods on one node | Maximum |
| spread | Round-robin across nodes | Minimum |
| adversarial | Heavy services + target on one node | High |
| random | Random per-deployment (seed=42) | Variable |
| best-fit | Bin-packing into fewest nodes | High |
| dependency-aware | Co-locate communicating services | Moderate |

---

## 8. File Organization

```
chaosprobe/
  chaosprobe/
    __init__.py              # version 0.1.0
    cli.py                   # CLI entry point (~273 lines)
    k8s.py                   # Singleton k8s config loader
    commands/
      shared.py              # Neo4j option decorators, shared helpers
      run_cmd.py             # run command (~576 lines)
      init_cmd.py            # init command (~297 lines)
      delete_cmd.py          # delete command (~152 lines)
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
      runner.py              # ChaosCenter GraphQL experiment execution (~529 lines)
      manifest.py            # Argo Workflow manifest generation (~194 lines)
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
      strategy_runner.py     # RunContext + execute_strategy() (~617 lines)
      run_phases.py          # Preflight, graph init, result writing (~669 lines)
      probers.py             # Continuous prober lifecycle (~214 lines)
      portforward.py         # Port-forward management (~123 lines)
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
      setup.py               # LitmusSetup main class (~1000 lines)
      vagrant.py             # _VagrantMixin (~591 lines)
      components.py          # _ComponentsMixin (~439 lines)
      chaoscenter.py         # _ChaosCenterMixin (~520 lines)
      chaoscenter_api.py     # _ChaosCenterAPIMixin (~801 lines)
    probes/
      builder.py             # RustProbeBuilder
      templates.py           # Cargo.toml, main.rs, Dockerfile templates
    storage/
      neo4j_store.py         # Neo4j graph store (~111 lines)
      neo4j_writer.py        # Write operations (~969 lines)
      neo4j_reader.py        # Read operations (~879 lines)
    graph/
      analysis.py            # High-level graph analysis functions
  scenarios/
    online-boutique/
      deploy/                # 12 microservice manifests
      placement-experiment.yaml
      contention-*/          # CPU, memory, IO, network, Redis, latency, throughput variants
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
- `neo4j` (>=5.0.0) — Neo4j driver
- `pyarrow` (>=12.0.0) — Parquet export

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

---

## 10. Neo4j Graph Schema

### Node Labels & Key Properties

| Node Label | Key Properties | Description |
|---|---|---|
| `K8sNode` | `name`, `cpu`, `memory`, `control_plane` | Kubernetes cluster nodes |
| `Deployment` | `name`, `namespace`, `replicas` | Kubernetes deployments |
| `Service` | `name` | Microservices in the dependency graph |
| `ChaosRun` | `run_id`, `name` (display: `strategy (verdict)`), `session_id`, `strategy`, `timestamp`, `verdict`, `resilience_score`, `duration_s`, `mean_recovery_ms`, `median_recovery_ms`, `min_recovery_ms`, `max_recovery_ms`, `p95_recovery_ms`, `recovery_count`, `completed_cycles`, `incomplete_cycles`, `total_restarts`, `total_experiments`, `passed_experiments`, `failed_experiments`, `load_profile`, `load_total_requests`, `load_avg_response_ms`, `load_p95_response_ms`, `load_error_rate`, `load_rps`, `node_name`, `node_capacity_cpu`, `node_capacity_memory`, `scenario_json`, `event_timeline` | Experiment run with all summary data |
| `PlacementStrategy` | `name` | Placement strategy (colocate/spread/random/adversarial/best-fit/dependency-aware) |
| `RecoveryCycle` | `run_id`, `name` (display: `cycle #N (Xms)`), `seq`, `deletion_time`, `scheduled_time`, `ready_time`, `deletion_to_scheduled_ms`, `scheduled_to_ready_ms`, `total_recovery_ms` | Per-pod deletion→scheduled→ready timing |
| `ExperimentResult` | `run_id`, `name` (display: `experiment (verdict)`), `experiment_name`, `engine_name`, `phase`, `verdict`, `probe_success_pct`, `fail_step` | LitmusChaos experiment outcome |
| `ProbeResult` | `run_id`, `name` (display: `probe (verdict)`), `probe_name`, `type`, `mode`, `verdict`, `description` | Individual probe pass/fail |
| `MetricsPhase` | `run_id`, `name` (display: `type: phase`), `metric_type`, `phase`, `sample_count`, `mean_cpu_millicores`, `max_cpu_millicores`, `mean_memory_bytes`, `max_memory_bytes`, `routes` (JSON), `metrics_json` | Aggregated metrics per phase (baseline/during/after) |
| `PodSnapshot` | `run_id`, `name`, `phase`, `node`, `restart_count`, `conditions` (JSON) | Pod state at collection time |
| `MetricsSample` | `run_id`, `name` (display: `#seq phase`), `timestamp`, `phase`, `strategy`, `seq`, `recovery_in_progress`, `recovery_cycle_id`, `data` (JSON) | Individual time-series data point |
| `AnomalyLabel` | `run_id`, `name` (display: `fault (severity)`), `fault_type`, `category`, `resource`, `severity`, `target_service`, `target_node`, `target_namespace`, `start_time`, `end_time`, `duration_s`, `parameters` (JSON), `observed_cycle_count`, `observed_completed_cycles`, `observed_incomplete_cycles` | Ground-truth fault label for ML |
| `CascadeEvent` | `run_id`, `name` (display: `cascade #N → service`), `seq`, `data_json` | Failure propagation event |
| `ContainerLog` | `run_id`, `name` (display: `pod/container`), `pod_name`, `container_name`, `restart_count`, `current_log`, `previous_log` | Container log snapshot |

### Relationships

| Relationship | From | To | Properties | Description |
|---|---|---|---|---|
| `DEPENDS_ON` | `Service` | `Service` | `port`, `protocol`, `description` | Service dependency graph |
| `EXPOSES` | `Deployment` | `Service` | — | Deployment exposes a service |
| `SCHEDULED_ON` | `Deployment` | `K8sNode` | `run_id` | Placement assignment per run |
| `USED_STRATEGY` | `ChaosRun` | `PlacementStrategy` | — | Which strategy a run used |
| `HAS_RECOVERY_CYCLE` | `ChaosRun` | `RecoveryCycle` | — | Recovery timing data |
| `HAS_RESULT` | `ChaosRun` | `ExperimentResult` | — | Experiment outcomes |
| `HAS_PROBE` | `ExperimentResult` | `ProbeResult` | — | Probe results per experiment |
| `HAS_METRICS_PHASE` | `ChaosRun` | `MetricsPhase` | — | Phase-aggregated metrics |
| `HAS_POD_SNAPSHOT` | `ChaosRun` | `PodSnapshot` | — | Pod state snapshots |
| `HAS_SAMPLE` | `ChaosRun` | `MetricsSample` | — | Time-series data points |
| `HAS_ANOMALY_LABEL` | `ChaosRun` | `AnomalyLabel` | — | Ground-truth fault labels |
| `HAS_CASCADE_EVENT` | `ChaosRun` | `CascadeEvent` | — | Failure propagation |
| `HAS_CONTAINER_LOG` | `PodSnapshot` or `ChaosRun` | `ContainerLog` | — | Container logs |
| `RUNNING_ON` | `PodSnapshot` | `K8sNode` | — | Pod-to-node assignment |
| `AFFECTS` | `AnomalyLabel` | `Service` | — | Services affected by fault |

### Schema Constraints & Indexes

Created by `Neo4jStore.ensure_schema()`:

**Uniqueness constraints**: `K8sNode.name`, `Deployment.name`, `Service.name`, `ChaosRun.run_id`, `PlacementStrategy.name`

**Indexes**: `RecoveryCycle.run_id`, `ExperimentResult.run_id`, `MetricsPhase.run_id`, `PodSnapshot.run_id`, `MetricsSample.run_id`, `MetricsSample.timestamp`, `AnomalyLabel.run_id`, `CascadeEvent.run_id`, `ContainerLog.run_id`, `ChaosRun.session_id`, `ProbeResult.run_id`

---

## 11. Anomaly Detection & ML Export

### Overview

ChaosProbe stores ground-truth anomaly labels alongside time-series metrics, enabling supervised anomaly detection. The pipeline works as follows:

1. **During chaos execution**: Continuous probers sample latency, resources, Redis, disk, and Prometheus metrics every few seconds. Each sample is stored as a `MetricsSample` node.
2. **After execution**: `anomaly_labels.py` generates `AnomalyLabel` nodes from the chaos scenario metadata (fault type, target service, time window, severity).
3. **At query time**: `MetricsSample` timestamps are compared against `AnomalyLabel` time windows to produce per-sample labels (`"pod-delete"`, `"pod-cpu-hog"`, or `"none"`).

### MetricsSample Data Fields

Each `MetricsSample.data` JSON blob can contain:

| Field Pattern | Example | Source |
|---|---|---|
| `latency:<route>:ms` | `latency:frontend→productcatalog:ms` | Latency prober |
| `latency:<route>:error` | `latency:frontend→productcatalog:error` | Latency prober (1=error, 0=ok) |
| `node_cpu_millicores` | `250.5` | Resource prober |
| `node_cpu_percent` | `12.5` | Resource prober |
| `node_memory_bytes` | `2147483648` | Resource prober |
| `node_memory_percent` | `52.3` | Resource prober |
| `pod_total_cpu_millicores` | `180.0` | Resource prober |
| `pod_total_memory_bytes` | `1073741824` | Resource prober |
| `pod_count` | `11` | Resource prober |
| `redis:<op>:ops_per_s` | `redis:SET:ops_per_s` | Redis prober |
| `redis:<op>:latency_ms` | `redis:SET:latency_ms` | Redis prober |
| `disk:<op>:ops_per_s` | `disk:write:ops_per_s` | Disk prober |
| `disk:<op>:bytes_per_s` | `disk:write:bytes_per_s` | Disk prober |
| `timestamp` | `2026-04-17T08:06:42Z` | All probers |
| `phase` | `PreChaos` / `DuringChaos` / `PostChaos` | All probers |
| `strategy` | `colocate` | Run context |
| `recovery_in_progress` | `true` / `false` | Recovery watcher |
| `recovery_cycle_id` | `3` | Recovery watcher |

### AnomalyLabel Fields

| Field | Description | Example |
|---|---|---|
| `fault_type` | LitmusChaos experiment name | `pod-delete`, `pod-cpu-hog` |
| `category` | Anomaly category | `availability`, `saturation`, `network` |
| `resource` | Affected resource type | `pod`, `cpu`, `memory`, `bandwidth`, `latency` |
| `severity` | Impact severity | `critical`, `high`, `medium`, `low` |
| `target_service` | Service under fault injection | `productcatalogservice` |
| `target_node` | Node where target is scheduled | `worker-1` |
| `start_time` / `end_time` | Fault injection time window | ISO-8601 timestamps |
| `affected_services` | Upstream services impacted (via `AFFECTS` edges) | `[frontend, checkoutservice]` |

### Manual Anomaly Investigation Workflow

Step-by-step guide for investigating anomalies in Neo4j Browser. Run each query **separately** (`:param` must be its own execution).

**Investigation order:**

```
ChaosRun (score/verdict)
  → ProbeResult           (what failed and why?)
  → AnomalyLabel          (what fault, exact time window?)
  → RecoveryCycle          (per-kill scheduling + startup timing)
  → MetricsSample          (recovery_in_progress moments with full telemetry)
  → MetricsPhase           (pre vs during vs post-chaos comparison)
  → CascadeEvent           (fault propagation across services)
  → PodSnapshot → K8sNode  (node contention — which pods were co-located?)
  → Cross-strategy comparison
```

#### Step 0 — Find your runs

```cypher
// Lists all runs sorted by time. Look for low scores or FAIL verdicts.
MATCH (r:ChaosRun)
RETURN r.run_id, r.strategy, r.resilience_score, r.verdict,
       r.mean_recovery_ms, r.timestamp
ORDER BY r.timestamp DESC
```

#### Step 1 — Set the run to investigate

Run this **separately** before all queries below. Change the run_id to the one you're investigating.

```
:param {rid: "run-2026-04-17-130553-45e6c3"}
```

#### Step 2 — What probes passed/failed? (explains the score)

```cypher
// Each probe has a different tolerance: strict ≈6s, moderate ≈9s, edge ≈75s.
// Failed probes tell you how long the service was degraded.
// The description shows the actual HTTP status code received.
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_RESULT]->(e:ExperimentResult)
      -[:HAS_PROBE]->(p:ProbeResult)
RETURN p.probe_name, p.verdict, p.description
ORDER BY p.probe_name
```

**Score interpretation** (6 probes → 7 possible scores):

| Score | Meaning |
|---|---|
| 100% | All probes passed — no visible disruption |
| 83% | 5/6 — one strict failed, recovery ~6–9s |
| 66% | 4/6 — both strict failed, recovery ~9s+ |
| 50% | 3/6 — moderate also failed |
| 33% | 2/6 — only edge + healthz passed |
| 16% | 1/6 — only healthz passed (node alive, service down) |
| 0% | Total disruption |

#### Step 3 — What fault was injected, when exactly?

```cypher
// Ground-truth anomaly label: fault type, severity, exact start/end times,
// target service/node, and which upstream services were affected.
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel)
OPTIONAL MATCH (a)-[:AFFECTS]->(s:Service)
RETURN a.fault_type, a.category, a.severity,
       a.start_time, a.end_time, a.duration_s,
       a.target_service, a.target_node,
       collect(s.name) AS affected_services
```

#### Step 4 — Recovery cycle timing (per pod kill → ready)

```cypher
// Each row = one pod deletion + rescheduling event.
// deletion_to_scheduled_ms: time to find a node (scheduling pressure)
// scheduled_to_ready_ms: time to start container (resource contention)
// total_recovery_ms: full outage window per kill
// Negative deletion_to_scheduled: new pod already scheduling before
//   old one's deletion event was processed (K8s controller proactive).
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_RECOVERY_CYCLE]->(c:RecoveryCycle)
RETURN c.seq, c.deletion_time, c.ready_time,
       c.deletion_to_scheduled_ms, c.scheduled_to_ready_ms,
       c.total_recovery_ms
ORDER BY c.seq
```

#### Step 5 — Time-series samples during active recovery

```cypher
// Shows only samples where a pod was actively recovering.
// The data JSON contains latency, CPU, memory, Redis, disk values.
// Null values = prober couldn't reach the target pod (it was dead).
// Phases are: "pre-chaos", "during-chaos", "post-chaos" (lowercase, hyphenated).
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_SAMPLE]->(s:MetricsSample)
WHERE s.phase = "during-chaos" AND s.recovery_in_progress = true
RETURN s.timestamp, s.recovery_cycle_id, s.data
ORDER BY s.timestamp
```

#### Step 6 — Phase comparison (pre vs during vs post)

```cypher
// Aggregated metrics per phase. Compare pre-chaos baselines against
// during-chaos values: CPU spikes, latency increases, memory pressure.
// metric_type: latency | resources | redis | disk | prometheus
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_METRICS_PHASE]->(m:MetricsPhase)
RETURN m.metric_type, m.phase, m.sample_count,
       m.mean_cpu_millicores, m.max_cpu_millicores,
       m.mean_memory_bytes, m.routes
ORDER BY m.metric_type, m.phase
```

#### Step 7 — Cascade propagation

```cypher
// Per-route degradation timing: when each route first degraded,
// peak latency, and when it recovered. Reveals fault propagation
// across the service dependency chain.
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_CASCADE_EVENT]->(c:CascadeEvent)
RETURN c.seq, c.data_json
ORDER BY c.seq
```

#### Step 8 — Pod placement and node contention

```cypher
// Shows which node each pod was on and node resource capacity.
// For colocate: all pods on one node. For spread: distributed.
MATCH (r:ChaosRun {run_id: $rid})-[:HAS_POD_SNAPSHOT]->(p:PodSnapshot)
      -[:RUNNING_ON]->(n:K8sNode)
RETURN n.name, collect(p.name) AS pods, n.cpu, n.memory
```

#### Step 9 — Cross-strategy comparison

```cypher
// Compares all strategies. Shows which placement had the best/worst
// resilience and recovery. Run AFTER investigating individual runs.
MATCH (r:ChaosRun)-[:USED_STRATEGY]->(s:PlacementStrategy)
RETURN s.name AS strategy,
       count(r) AS runs,
       avg(r.resilience_score) AS avg_score,
       avg(r.mean_recovery_ms) AS avg_recovery_ms,
       collect(r.verdict) AS verdicts
ORDER BY avg_score DESC
```

#### Repeat

Change `:param {rid: "..."}` to a different strategy's run ID (e.g. colocate, default, adversarial) and re-run Steps 2–8 to compare how placement affected each dimension.

---

### Cypher Query Cookbook

> **Neo4j Browser vs Python driver**: Queries below use `$rid`, `$service`, etc.
> In the **Python driver** these are passed as keyword arguments (e.g. `session.run(query, rid="20260417-080642")`).
> In the **Neo4j Browser** you must set parameters first:
> ```
> :param {rid: "20260417-080642"}
> ```
> Then run the query. To find valid run IDs, use query 10 below.

#### 0. List all run IDs (find values for `$rid`)

```cypher
MATCH (e:ChaosRun)
RETURN e.run_id AS run_id, e.strategy AS strategy,
       e.timestamp AS timestamp, e.verdict AS verdict
ORDER BY e.timestamp DESC
```

#### 1. ML-ready labeled dataset (supervised anomaly detection)

Join time-series samples with anomaly labels. Each row gets `anomaly_label = fault_type` if the sample falls within the fault window, otherwise `"none"`.

```cypher
// No parameters needed — returns all runs
MATCH (e:ChaosRun)-[:HAS_SAMPLE]->(s:MetricsSample)
OPTIONAL MATCH (e)-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel)
RETURN s.run_id AS run_id, s.timestamp AS timestamp,
       s.phase AS phase, s.strategy AS strategy,
       s.data AS data, e.resilience_score AS resilience_score,
       e.verdict AS verdict, a.fault_type AS fault_type,
       a.start_time AS anomaly_start, a.end_time AS anomaly_end
ORDER BY s.run_id, s.seq
```

Filter by strategy or run (set params first in Neo4j Browser):

```
:param {strategy: "colocate"}
```

```cypher
MATCH (e:ChaosRun)-[:HAS_SAMPLE]->(s:MetricsSample)
WHERE e.strategy = $strategy
OPTIONAL MATCH (e)-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel)
RETURN s.run_id AS run_id, s.timestamp AS timestamp,
       s.phase AS phase, s.strategy AS strategy,
       s.data AS data, e.resilience_score AS resilience_score,
       e.verdict AS verdict, a.fault_type AS fault_type,
       a.start_time AS anomaly_start, a.end_time AS anomaly_end
ORDER BY s.run_id, s.seq
```

#### 2. Anomaly labels with affected services

```
:param {rid: "20260417-080642"}
```

```cypher
MATCH (e:ChaosRun {run_id: $rid})-[:HAS_ANOMALY_LABEL]->(a:AnomalyLabel)
OPTIONAL MATCH (a)-[:AFFECTS]->(s:Service)
RETURN properties(a) AS label, collect(s.name) AS affected_services
```

#### 3. Cascade / failure propagation timeline

```
:param {rid: "20260417-080642"}
```

```cypher
MATCH (e:ChaosRun {run_id: $rid})-[:HAS_CASCADE_EVENT]->(c:CascadeEvent)
RETURN c.data_json AS event ORDER BY c.seq
```

#### 4. Blast radius — upstream services affected by a failure

```
:param {service: "productcatalogservice"}
```

```cypher
MATCH path = (t:Service {name: $service})<-[:DEPENDS_ON*1..3]-(upstream:Service)
RETURN upstream.name AS name, length(path) AS hops ORDER BY hops
```

#### 5. Phase-based anomaly detection (baseline vs chaos vs post-chaos)

Compare aggregated metrics across phases to detect deviations:

```
:param {rid: "20260417-080642"}
```

```cypher
MATCH (e:ChaosRun {run_id: $rid})-[:HAS_METRICS_PHASE]->(m:MetricsPhase)
RETURN m.metric_type AS type, m.phase AS phase, m.sample_count AS samples,
       m.mean_cpu_millicores AS mean_cpu, m.max_cpu_millicores AS max_cpu,
       m.mean_memory_bytes AS mean_mem, m.routes AS latency_routes
ORDER BY m.metric_type, m.phase
```

#### 6. Recovery cycle analysis

```
:param {rid: "20260417-080642"}
```

```cypher
MATCH (e:ChaosRun {run_id: $rid})-[:HAS_RECOVERY_CYCLE]->(c:RecoveryCycle)
RETURN c.seq AS cycle, c.deletion_time AS deleted, c.scheduled_time AS scheduled,
       c.ready_time AS ready, c.total_recovery_ms AS recovery_ms,
       c.deletion_to_scheduled_ms AS scheduling_ms,
       c.scheduled_to_ready_ms AS startup_ms
ORDER BY c.seq
```

#### 7. Compare recovery across placement strategies

```cypher
// No parameters needed
MATCH (e:ChaosRun)-[:USED_STRATEGY]->(s:PlacementStrategy)
RETURN s.name AS strategy, e.run_id AS run_id,
       e.resilience_score AS score, e.mean_recovery_ms AS mean_recovery,
       e.p95_recovery_ms AS p95_recovery, e.verdict AS verdict
ORDER BY s.name, e.timestamp
```

#### 8. Colocation analysis — resource contention hotspots

```
:param {rid: "20260417-080642"}
```

```cypher
MATCH (d:Deployment)-[:SCHEDULED_ON {run_id: $rid}]->(n:K8sNode)
WITH n, collect(d.name) AS deps WHERE size(deps) > 1
RETURN n.name AS node, deps AS colocated_deployments ORDER BY size(deps) DESC
```

#### 9. Critical path — longest dependency chain

```cypher
// No parameters needed
MATCH path = (a:Service)-[:DEPENDS_ON*]->(b:Service)
WHERE NOT (b)-[:DEPENDS_ON]->()
RETURN [n IN nodes(path) | n.name] AS chain, length(path) AS depth
ORDER BY depth DESC LIMIT 1
```

#### 10. Database status — node counts per label

```cypher
MATCH (n:ChaosRun) RETURN count(n);
MATCH (n:MetricsSample) RETURN count(n);
MATCH (n:AnomalyLabel) RETURN count(n);
MATCH (n:CascadeEvent) RETURN count(n);
// ... repeat for each label
```

### Programmatic Export

#### Python API

```python
from chaosprobe.output.ml_export import export_from_neo4j, write_dataset

# Export all runs
rows = export_from_neo4j(uri="bolt://localhost:7687", user="neo4j", password="neo4j")

# Filter by strategy
rows = export_from_neo4j(strategy="colocate")

# Filter by run IDs
rows = export_from_neo4j(run_ids=["20260417-080642", "20260417-095752"])

# Write to CSV or Parquet
write_dataset(rows, "anomaly_dataset.csv", format="csv")
write_dataset(rows, "anomaly_dataset.parquet", format="parquet")  # requires pyarrow
```

#### CLI

```bash
# Export all runs to CSV
chaosprobe ml-export --neo4j-uri bolt://localhost:7687 -o dataset.csv

# Export specific strategy to Parquet
chaosprobe ml-export --neo4j-uri bolt://localhost:7687 -o dataset.parquet \
    --format parquet --strategy colocate
```

#### Output Columns

Each exported row represents one time bucket (default 5s resolution). Metadata columns: `run_id`, `timestamp`, `phase`, `strategy`, `resilience_score`, `overall_verdict`, `anomaly_label`. Feature columns match the **MetricsSample Data Fields** table above (`latency:<route>:ms`, `node_cpu_millicores`, `redis:<op>:ops_per_s`, etc.).

### Supported Anomaly Types

From `EXPERIMENT_TO_ANOMALY` in `anomaly_labels.py`:

| Fault Type | Category | Resource | Severity |
|---|---|---|---|
| `pod-delete` | availability | pod | critical |
| `pod-cpu-hog` | saturation | cpu | high |
| `pod-memory-hog` | saturation | memory | high |
| `pod-network-loss` | network | bandwidth | high |
| `pod-network-latency` | network | latency | medium |
| `pod-network-corruption` | network | integrity | medium |
| `pod-network-duplication` | network | bandwidth | low |
| `pod-io-stress` | saturation | disk | medium |
| `disk-fill` | saturation | disk | high |
| `node-cpu-hog` | saturation | cpu | critical |
| `node-memory-hog` | saturation | memory | critical |
| `node-drain` | availability | node | critical |
| `kubelet-service-kill` | availability | kubelet | critical |


### AI Model Analysis Flow

This section documents how an AI model (LLM) reads experiment data from Neo4j and performs fault analysis, anomaly detection, and remediation reasoning. Two prompt files drive this process:

- `prompts/ANALYSIS_PROMPT.md` — one-shot fault analysis of a single run or session
- `prompts/FIX_LOOP.md` — continuous autonomous operator (run experiments → diagnose → fix → repeat)

#### Data Retrieval Path

The AI model does **not** query Neo4j directly. Instead, data reaches the model through two extraction paths:

```
Path A: JSON reconstruction (for LLM context)
──────────────────────────────────────────────
Neo4j  ──▶  chaosprobe graph details <run-id> --json
            │
            └──▶  get_run_output(run_id)   ← neo4j_reader.py
                  │
                  ├── ChaosRun properties (verdict, score, load stats, timestamps)
                  ├── RecoveryCycle[] (per-pod deletion→scheduled→ready timing)
                  ├── ExperimentResult[] + ProbeResult[] (verdicts)
                  ├── MetricsPhase[] (pre/during/post aggregates per metric type)
                  ├── PodSnapshot[] (pod state at collection time)
                  ├── ContainerLog[] (application logs)
                  ├── MetricsSample[] → reconstructed time-series arrays
                  ├── AnomalyLabel[] + AFFECTS→Service (ground-truth labels)
                  └── CascadeEvent[] (failure propagation)
                  │
                  ▼
            Full JSON document (schema v2.0.0)
            fed into LLM context as structured input

Path B: Summary JSON files (filesystem)
────────────────────────────────────────
results/<timestamp>/
  ├── summary.json        ← all strategies, comparison table
  ├── baseline.json       ← per-strategy: iterations[], aggregated metrics
  ├── colocate.json
  ├── spread.json
  └── ...
```

The AI reads the JSON output and applies the analysis prompt's instructions.

#### What the AI Model Detects

The analysis prompt (`ANALYSIS_PROMPT.md`) instructs the model to perform 9 analysis tasks on the structured data:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    AI READS FROM NEO4J/JSON                             │
│                                                                         │
│  Input: Full run JSON (schema v2.0.0) with all metrics + labels         │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  1. FAULT IDENTIFICATION                                        │    │
│  │     ─ When: exact fault window from RecoveryCycle.deletionTime  │    │
│  │     ─ What: fault type, target, chaos params from scenario      │    │
│  │     ─ Cycle count validation: observed vs expected              │    │
│  │       (floor(TOTAL_CHAOS_DURATION / CHAOS_INTERVAL))            │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  2. ROOT CAUSE ANALYSIS                                         │    │
│  │     ─ Maps fault → K8s mechanism (pod-delete → force terminate  │    │
│  │       → Deployment controller → scheduling → readiness probe)   │    │
│  │     ─ Checks replica count (1 replica + 100% affected = total   │    │
│  │       outage per cycle)                                         │    │
│  │     ─ Pre-chaos resource headroom from MetricsPhase baseline    │    │
│  │     ─ Placement effect: which node, recovery correlation        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  3. IMPACT ASSESSMENT (multi-signal fusion)                     │    │
│  │                                                                  │    │
│  │  Primary:   Load generator metrics (RPS, error rate, P95/P99)   │    │
│  │  Secondary: Probe verdicts (6 probes at different sensitivities) │    │
│  │  Tertiary:  Cascade error counts per route                      │    │
│  │  Control:   Redis/Disk stability confirms blast radius is       │    │
│  │             limited to target service dependency chain           │    │
│  │                                                                  │    │
│  │  → Classifies: Isolated / Cascading / Systemic                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  4. TEMPORAL ANALYSIS                                           │    │
│  │     ─ Phase comparison table (MetricsPhase pre vs during vs     │    │
│  │       post for CPU, memory, throttling, network)                │    │
│  │     ─ Event timeline reconstruction from raw K8s events         │    │
│  │     ─ Recovery-metric correlation: CPU spikes during            │    │
│  │       recovery_in_progress=true samples                         │    │
│  │     ─ Impact duration: total downtime / chaos duration          │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  5. PROBE ANALYSIS                                              │    │
│  │     ─ Per-probe verdict table across all strategies             │    │
│  │     ─ Score interpretation: 16% = only healthz survived         │    │
│  │       66% = moderate+edge+cart+healthz passed                   │    │
│  │     ─ What differentiates strategy scores                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  6. DIAGNOSIS & MITIGATION                                      │    │
│  │     ─ Immediate: increase replicas, add PDB, circuit breakers   │    │
│  │     ─ Preventive: pod anti-affinity, readiness probe tuning,    │    │
│  │       pre-pulled images                                         │    │
│  │     ─ Placement recommendation based on data                    │    │
│  │     ─ Observability fixes (broken probers, scrape intervals)    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  7. CROSS-RUN COMPARISON (within session)                       │    │
│  │     ─ Strategy effectiveness table: resilience, recovery,       │    │
│  │       load error rate, RPS, cascade errors                      │    │
│  │     ─ Identifies confounded comparisons (different params)      │    │
│  │     ─ Fair comparison groups (same TOTAL_CHAOS_DURATION)        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  8. CROSS-EXPERIMENT PATTERNS (multiple fault types)            │    │
│  │     ─ Fault type comparison (availability vs saturation vs net) │    │
│  │     ─ Service resilience ranking                                │    │
│  │     ─ Strategy × fault-type interaction matrix                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                          │                                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  9. EXECUTIVE SUMMARY                                           │    │
│  │     ─ 3-5 sentences: what was tested, what broke, severity,     │    │
│  │       single most impactful remediation action                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Signal Hierarchy

The analysis prompt defines which data signals are reliable and which are not, based on known data quality issues:

| Signal | Reliability | Used For | Known Issues |
|---|---|---|---|
| Recovery cycles (`RecoveryCycle`) | **High** | Fault timing, recovery duration | `deletionToScheduled_ms` can be negative (timestamp precision); use `totalRecovery_ms` |
| Load generator (`ChaosRun.load_*`) | **High** | User-perspective impact (RPS, errors, P95) | None — most reliable signal |
| Probe verdicts (`ProbeResult`) | **High** | Resilience scoring, pass/fail per sensitivity tier | Strict probes always fail with single-replica pod-delete (by design) |
| Node resources (`MetricsPhase` resources) | **Medium** | CPU/memory contention analysis | `pod_total_*` frequently null (metrics-server limitation) |
| Prometheus (`MetricsPhase` prometheus) | **Medium** | Cluster-wide trends | `pod_ready_count` stays 16.0 during chaos (10s scrape too coarse for 1-2s recovery) |
| Redis throughput | **Control** | Confirms blast radius doesn't reach data layer | Stable across all phases; used as negative evidence |
| Cascade timeline (`CascadeEvent`) | **Low** | Route-level error propagation | `peakLatency_ms` may be null for routes not targeted by probes |
| Latency prober (`MetricsPhase` latency) | **Medium** | Route-level HTTP response time | Derives target service from URL; pre-flight validates reachability |
| Disk prober (`MetricsPhase` disk) | **Medium** | Sequential I/O throughput | Auto-discovers exec-capable pods; excludes chaos target service |

#### Autonomous Operator Flow (FIX_LOOP.md)

The `FIX_LOOP.md` prompt drives an autonomous loop where the AI reads results, diagnoses, fixes code, and re-runs:

```
                         ┌──────────────────────┐
                         │  1. chaosprobe delete │ ← clean slate
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │  2. chaosprobe init   │ ← install infra
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │  3. Verify cluster    │ ← nodes, pods, Neo4j,
                         │     health            │   Prometheus, ChaosCenter
                         └──────────┬───────────┘
                                    │
                                    ▼
                ┌───────────────────────────────────────┐
                │  4. chaosprobe run                     │
                │     -s random,spread --iterations 2    │
                └───────────────────┬───────────────────┘
                                    │
                                    ▼
                ┌───────────────────────────────────────┐
                │  5. AI reads results:                  │
                │     ─ cat results/<ts>/summary.json    │
                │     ─ Per-strategy JSON files          │
                │     ─ chaosprobe graph details --json  │
                │     ─ chaosprobe graph status          │
                │                                        │
                │  Diagnoses:                            │
                │     ─ Score=0 for all strategies?      │
                │       → probes timing out, check RBAC  │
                │     ─ No recovery events?              │
                │       → ChaosCenter not executing      │
                │     ─ meanRecovery > 30s?              │
                │       → node resource pressure         │
                │     ─ Identical scores across runs?    │
                │       → experiment not differentiating │
                └───────────────────┬───────────────────┘
                                    │
                          ┌─────────┴─────────┐
                          │                   │
                    Issue found?          All healthy
                          │                   │
                          ▼                   ▼
                ┌──────────────────┐  ┌──────────────────┐
                │  6. Fix:         │  │  7. Commit        │
                │  ─ Code bug fix  │  │     results +     │
                │  ─ Cluster fix   │  │     code fixes    │
                │  ─ Config fix    │  │                    │
                │  ─ Run tests     │  │  8. Maintenance   │
                │                  │  │     cycle          │
                └───────┬──────────┘  └────────┬──────────┘
                        │                      │
                        └──────────┬───────────┘
                                   │
                                   ▼
                         ┌──────────────────────┐
                         │  Loop back to step 1  │
                         └───────────────────────┘
```

#### Concrete Example: AI Reads a Run

Given a completed run with `run_id = "run-20260417-080655"`, the AI reads the data and reasons through it:

**1. Read run metadata:**
```bash
chaosprobe graph details run-20260417-080655-colocate-1 --json
```

Returns the full JSON document (~5-20KB) with all sections.

**2. AI identifies the fault:**
> "RecoveryCycle[0].deletionTime = 2026-04-17T08:07:42Z. Fault type: pod-delete targeting
> productcatalogservice on worker3. TOTAL_CHAOS_DURATION=120s, CHAOS_INTERVAL=5s.
> Expected ~24 cycles, observed 24 cycles. ✓"

**3. AI assesses impact using signal hierarchy:**
> "Load generator: 2,418 requests, 19.9% error rate, avg response 2,341ms, P99 30,001ms.
> This indicates significant user-facing degradation — 1 in 5 requests failed.
>
> Probe verdicts: frontend-healthz=Pass, frontend-homepage-strict=Fail,
> frontend-product-strict=Fail, frontend-cart=Pass, frontend-homepage-moderate=Pass,
> frontend-homepage-edge=Pass. Score: 66%.
>
> Redis: 67 ops/s write, 70 ops/s read, 0 errors — stable across phases. Blast radius
> limited to productcatalogservice dependency chain, not data layer."

**4. AI compares strategies from the session:**
> "colocate: 66% / 1529ms mean recovery / 19.9% error rate
>  spread:  66% / 1332ms mean recovery / 17.2% error rate
>  default: 16% / 1219ms mean recovery / 87.3% error rate ← dramatically worse
>
> Spread achieves equal resilience with lowest error rate. Default has fastest raw recovery
> but worst user impact — likely due to K8s scheduling decisions under contention."

**5. AI recommends remediation:**
> "Highest-impact fix: increase productcatalogservice replicas to 2 with
> PodDisruptionBudget minAvailable=1. With N+1 replicas, pod-delete of one replica
> leaves the service available, eliminating the 1-2s outage window per cycle.
> No placement strategy can fix single-replica pod-delete vulnerability."

#### Relation Detection Through Graph Traversal

The AI detects relationships between faults and impacts using the Neo4j graph structure:

```
Fault injection:
  ChaosRun --HAS_ANOMALY_LABEL--> AnomalyLabel {fault_type: "pod-delete",
                                                  target_service: "productcatalogservice"}

Affected services (via dependency graph):
  AnomalyLabel --AFFECTS--> Service {name: "frontend"}
  AnomalyLabel --AFFECTS--> Service {name: "checkoutservice"}
  AnomalyLabel --AFFECTS--> Service {name: "recommendationservice"}

  (because: frontend --DEPENDS_ON--> productcatalogservice
            checkoutservice --DEPENDS_ON--> productcatalogservice
            recommendationservice --DEPENDS_ON--> productcatalogservice)

Placement context:
  ChaosRun --USED_STRATEGY--> PlacementStrategy {name: "colocate"}
  Deployment {name: "productcatalogservice"} --SCHEDULED_ON {run_id}--> K8sNode {name: "worker3"}

Recovery causality:
  ChaosRun --HAS_RECOVERY_CYCLE--> RecoveryCycle {seq: 0, total_recovery_ms: 1340}
  ChaosRun --HAS_SAMPLE--> MetricsSample {timestamp: T, recovery_in_progress: true}
    → Sample shows CPU spike at same timestamp as recovery cycle
    → AI correlates: "pod startup on worker3 caused 320→410 millicores CPU spike"

Cascade evidence:
  ChaosRun --HAS_CASCADE_EVENT--> CascadeEvent
    → "frontend→productcatalog route: 47 errors during 24 cycles"
    → "frontend→cart route: 0 errors" (independent of target)
    → AI concludes: "cascade is service-specific, not node-wide"
```

This graph structure allows the AI to trace from a fault injection through the dependency graph to quantified user impact, with placement and recovery context at every step.
