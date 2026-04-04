# ChaosProbe Technical Reference

## 1. System Overview

ChaosProbe is a Python framework for automated Kubernetes chaos testing with AI-consumable output. It wraps LitmusChaos to run native ChaosEngine experiments, collects real-time pod recovery metrics, and stores all data in a Neo4j graph database for machine-learning feedback loops.

**Core loop**: deploy manifests -> run chaos experiments -> collect metrics -> store in Neo4j -> AI reads data, edits manifests, re-runs, compares.

```
ChaosProbe CLI (cli.py, ~3200 lines)
      |
      +-- Cluster Manager (provisioner/setup.py)
      |     +-- Vagrant (local dev: 3-node KVM/libvirt cluster)
      |     +-- Kubespray (production bare-metal/cloud)
      |
      +-- Setup Manager (provisioner/setup.py)
      |     Installs Helm, LitmusChaos, RBAC automatically
      |
      +-- Config Loader (config/loader.py)
      |     +-- Validator (config/validator.py)
      |     +-- Topology Parser (config/topology.py)
      |     Auto-classifies YAML files by kind field
      |     Loads optional cluster.yaml for provisioning config
      |     Extracts service dependencies from deployment env vars
      |
      +-- Infrastructure Provisioner (provisioner/kubernetes.py)
      |     Applies raw K8s manifests (Deployment, Service, etc.)
      |
      +-- Placement Engine
      |     +-- Strategy (placement/strategy.py)
      |     |   colocate, spread, random, antagonistic
      |     +-- Mutator (placement/mutator.py)
      |         nodeSelector injection, rollout management
      |
      +-- Chaos Runner (chaos/runner.py)
      |     Creates ChaosEngine CRDs, polls for completion
      |
      +-- Load Generator (loadgen/runner.py)
      |     Locust-based load generation with preset profiles
      |     (steady, ramp, spike) and CSV stats collection
      |
      +-- Metrics Collection
      |     +-- RecoveryWatcher (metrics/recovery.py)
      |     |   Real-time pod watch during chaos
      |     +-- Continuous Probers (latency, throughput, resources, Prometheus)
      |     +-- Anomaly Labels (metrics/anomaly_labels.py)
      |     +-- Cascade Timeline (metrics/cascade.py)
      |     +-- MetricsCollector (metrics/collector.py)
      |         Pod status, node info, unified output
      |
      +-- Result Collector (collector/result_collector.py)
      |     ChaosResult CRDs, probe verdicts, resilience score
      |
      +-- Output Generator (output/generator.py)
      |     +-- Comparison Engine (output/comparison.py)
      |     +-- Visualizer (output/visualize.py)
      |     |   Charts, heatmaps, HTML reports
      |     +-- ML Export (output/ml_export.py)
      |         Aligned CSV/Parquet datasets for ML
      |
      +-- Storage
      |     +-- Neo4j Graph Store (storage/neo4j_store.py) [primary]
      |     |   Topology, runs, metrics, time-series, anomaly labels
      |     +-- SQLite (storage/sqlite.py) [secondary]
      |         Tabular queries, CSV export
      |
      +-- Graph Analysis (graph/analysis.py)
            Blast radius, topology comparison, colocation impact,
            critical path analysis, strategy summary
```

---

## 2. Module Reference

### 2.1 Configuration (`chaosprobe/config/`)

#### loader.py

Loads scenario directories or single YAML files. Auto-classifies resources by their `kind` field: ChaosEngine kinds go to `experiments`, everything else to `manifests`.

| Function | Signature | Purpose |
|---|---|---|
| `load_scenario` | `(scenario_path: str) -> Dict` | Main entry point. Returns `{path, manifests, experiments, namespace, cluster (optional), probes (optional)}` |
| `_load_yaml_file` | `(filepath: Path) -> Tuple[List, List]` | Parses multi-document YAML, classifies by kind |
| `_load_yaml_directory` | `(dirpath: Path) -> Tuple[List, List]` | Loads all .yaml/.yml from directory |
| `_detect_namespace` | `(experiments: List) -> str` | Extracts namespace from ChaosEngine appinfo. Default: `"default"` |
| `_load_cluster_config` | `(dirpath: Path) -> Optional[Dict]` | Loads `cluster.yaml` if present in scenario dir |

**Constants**: `CHAOS_KINDS = {"ChaosEngine"}`, `CLUSTER_CONFIG_FILE = "cluster.yaml"`

#### topology.py

Dynamically extracts service-to-service dependencies from Kubernetes deployment manifests by parsing environment variables (e.g. `*_SERVICE_ADDR`, `*_ADDR`). Replaces the old hardcoded service dependency graph.

| Function | Signature | Purpose |
|---|---|---|
| `parse_topology_from_scenario` | `(scenario: Dict) -> List[ServiceRoute]` | Extracts routes from a loaded scenario dict (checks `manifests` key) |
| `parse_topology_from_directory` | `(deploy_dir: str) -> List[ServiceRoute]` | Loads all YAML files from a directory and extracts routes |
| `parse_topology_from_manifests` | `(manifests: List[Dict]) -> List[ServiceRoute]` | Extracts routes from a list of parsed manifest dicts |
| `_extract_dependencies_from_deployment` | `(deployment: Dict) -> List[ServiceRoute]` | Parses a single Deployment for env-var service references |
| `_infer_protocol` | `(target_service: str, port: str) -> str` | Infers protocol (`grpc` or `tcp`) from service name and port |
| `_env_name_to_description` | `(env_name: str) -> str` | Converts env name (e.g. `PRODUCT_CATALOG_SERVICE_ADDR`) to human description |

**Type alias**: `ServiceRoute = Tuple[str, str, str, str, str]` — `(source_service, target_service, target_host, protocol, description)`

**Pattern matching**: Recognizes `*_SERVICE_ADDR`, `*_ADDR`, `*_SERVICE_HOST` env vars and extracts the target service name from the address value (before `:`). The source service is the deployment name.

#### validator.py

Validates loaded scenarios for structural correctness before execution, including comprehensive validation of all LitmusChaos resilience probe types.

| Function | Purpose |
|---|---|
| `validate_scenario(scenario)` | Validates entire scenario. Raises `ValidationError` with aggregated errors |
| `_validate_chaos_engine(spec, filepath)` | Checks: apiVersion, kind, experiments list, applabel, chaosServiceAccount, probes |
| `_validate_probe(probe, filepath, exp_name)` | Validates probe name, type, mode, runProperties, and type-specific inputs |
| `_validate_run_properties(run_props, prefix)` | Checks: probeTimeout, interval, retry |
| `_validate_http_probe(probe, prefix)` | Validates httpProbe/inputs: url, method (get/post), criteria, responseCode |
| `_validate_cmd_probe(probe, prefix)` | Validates cmdProbe/inputs: command, comparator, optional source.image |
| `_validate_k8s_probe(probe, prefix)` | Validates k8sProbe/inputs: group, version, resource, namespace, operation |
| `_validate_prom_probe(probe, prefix)` | Validates promProbe/inputs: endpoint, query/queryPath, comparator |
| `_validate_comparator(comparator, prefix)` | Validates comparator block: type (string/int/float), criteria, value |
| `_validate_manifest(spec, filepath)` | Checks: apiVersion, kind, metadata.name |
| `_validate_cluster_config(cluster)` | Checks: provider (vagrant/kubespray), workers.count/cpu/memory/disk |

**Supported probe types**: `httpProbe`, `cmdProbe`, `k8sProbe`, `promProbe`

**Supported probe modes**: `SOT`, `EOT`, `Edge`, `Continuous`, `OnChaos`

**Probe type details**:

| Probe Type | Key Inputs | Use Case |
|---|---|---|
| `httpProbe` | `url`, `method` (get/post), `criteria`, `responseCode` | Health checks via HTTP GET/POST |
| `cmdProbe` | `command`, `comparator`, optional `source` image | Shell command health checks |
| `k8sProbe` | `group`, `version`, `resource`, `namespace`, `operation` (present/absent/create/delete) | Kubernetes resource state verification |
| `promProbe` | `endpoint`, `query`/`queryPath`, `comparator` | Prometheus metrics-based SLO checks |

---

### 2.2 Chaos Execution (`chaosprobe/chaos/`)

#### runner.py

Orchestrates ChaosEngine lifecycle: create, poll, collect status, cleanup.

**Class: `ChaosRunner(namespace, timeout=300, chaoscenter=None)`**

| Method | Purpose |
|---|---|
| `run_experiments(experiments)` | Runs all ChaosEngine experiments sequentially |
| `_run_single_experiment(engine_spec)` | Patches spec with unique suffix, creates CRD, waits for completion |
| `_wait_for_engine(engine_name, start_time)` | Polls engine status every 5s until completed/timeout |
| `_delete_chaos_engine(engine_name)` | Idempotent delete with finalizer cleanup |
| `_cleanup_managed_engines(exclude)` | Deletes leftover `managed-by=chaosprobe` engines from previous runs |
| `_register_with_chaoscenter(engine_spec, engine_name)` | Saves + runs experiment via ChaosCenter GraphQL API |
| `_build_workflow_manifest(engine_spec, engine_name, instance_id)` | Generates Argo Workflow YAML wrapping a ChaosEngine |
| `get_executed_experiments()` | Returns metadata for all executed experiments |

**ChaosCenter integration**: When the optional `chaoscenter` dict is provided (keys: `token`, `project_id`, `infra_id`, `gql_url`), each experiment is registered with ChaosCenter via the `saveChaosExperiment` and `runChaosExperiment` GraphQL mutations before the ChaosEngine CRD is created. This makes experiments visible in the ChaosCenter dashboard. Registration failures are logged but do not prevent direct CRD execution (graceful degradation).

**Kubernetes API**: Uses `CustomObjectsApi` for ChaosEngine CRUD on `litmuschaos.io/v1alpha1`.

---

### 2.3 Result Collection (`chaosprobe/collector/`)

#### result_collector.py

Collects ChaosResult CRDs and calculates resilience metrics. Supports all LitmusChaos probe types (httpProbe, cmdProbe, k8sProbe, promProbe) with type-aware parsing.

**Class: `ResultCollector(namespace)`**

| Method | Purpose |
|---|---|
| `collect(executed_experiments)` | Collects results for all executed experiments |
| `_collect_experiment_result(engine_name, exp_name)` | Gathers engine status, ChaosResult, verdict, probe success |
| `_get_chaos_result(engine_name, exp_name)` | Tries multiple naming patterns for ChaosResult lookup |
| `_parse_chaos_result(chaos_result)` | Extracts phase, verdict, probe success %, probe statuses |
| `_parse_probe_status(probe_status)` | Normalises probe type names and extracts per-phase verdicts |
| `_determine_verdict(result)` | Returns "Pass", "Fail", or "Awaited" |

**Probe type normalisation**: Maps LitmusChaos type names (e.g. `HTTPProbe`, `CmdProbe`, `K8sProbe`, `PromProbe`) to canonical names (`httpProbe`, `cmdProbe`, `k8sProbe`, `promProbe`).

**Module function: `calculate_resilience_score(results, weights=None) -> float`**
- Weighted average of probe success percentages (0-100)
- Default: equal weight (1.0) per experiment

---

### 2.4 Metrics Collection (`chaosprobe/metrics/`)

#### recovery.py - Real-Time Pod Watch

**Class: `RecoveryWatcher(namespace, deployment_name)`**

Runs a background thread using the Kubernetes watch API to observe pod lifecycle events in real-time. Records deletion and ready timestamps as they happen, guaranteeing capture regardless of event-store retention.

| Method | Purpose |
|---|---|
| `start()` | Snapshots current pods, starts background watch thread |
| `stop()` | Stops watch, finalizes any pending recovery cycle |
| `result()` | Returns structured recovery data with cycles and summary |

**Watch logic**:
1. DELETED event -> records `_pending_deletion` timestamp
2. ADDED/MODIFIED event where pod transitions from not-ready to ready -> closes the cycle
3. Extracts `PodScheduled` condition time for scheduling latency

**Recovery cycle output**:
```json
{
  "deletionTime": "ISO8601",
  "scheduledTime": "ISO8601",
  "readyTime": "ISO8601",
  "deletionToScheduled_ms": 120,
  "scheduledToReady_ms": 880,
  "totalRecovery_ms": 1000
}
```

**Summary statistics**: count, completedCycles, mean, median, min, max, p95 (all in ms).

#### collector.py - Unified Metrics Aggregator

**Class: `MetricsCollector(namespace)`**

Orchestrates post-experiment data collection and merges it with pre-collected watcher data.

| Method | Purpose |
|---|---|
| `collect(deployment_name, since_time, until_time, recovery_data=None, latency_data=None, redis_data=None, disk_data=None, resource_data=None, prometheus_data=None, collect_logs=False)` | Unified metrics with recovery, pod status, node info, and continuous prober data |
| `_collect_pod_status(deployment_name)` | Current pod phases, restart counts, conditions |
| `_collect_node_info(node_name)` | Node allocatable/capacity for CPU and memory |

**Output structure**:
```json
{
  "deploymentName": "checkoutservice",
  "timeWindow": {"start": "...", "end": "...", "duration_s": 167.3},
  "recovery": {"recoveryEvents": [...], "summary": {...}},
  "podStatus": {"pods": [...], "totalRestarts": 0},
  "eventTimeline": [...],
  "nodeInfo": {"nodeName": "worker1", "allocatable": {...}, "capacity": {...}}
}
```

---

### 2.5 Placement Engine (`chaosprobe/placement/`)

#### strategy.py

Defines four placement strategies and computes node assignments.

**Enum: `PlacementStrategy`**

| Strategy | Behavior |
|---|---|
| `colocate` | All deployments pinned to a single node (max resource contention) |
| `spread` | Round-robin across all schedulable nodes (min contention) |
| `random` | Random assignment per deployment (reproducible with seed) |
| `antagonistic` | Top-N resource-heavy deployments on one node, rest distributed |

**Dataclasses**:
- `NodeInfo(name, labels, allocatable_cpu_millicores, allocatable_memory_bytes, conditions_ready, taints)` - `.is_schedulable`, `.is_control_plane` properties
- `DeploymentInfo(name, replicas, cpu_request_millicores, memory_request_bytes, current_node)`
- `NodeAssignment(strategy, assignments, seed, metadata)` - serializable via `to_dict()`/`from_dict()`

**Entry point**: `compute_assignments(strategy, deployments, nodes, target_node=None, seed=None) -> NodeAssignment`

**Resource parsing**: "200m" -> 200 millicores, "128Mi" -> bytes, handles Ki/Mi/Gi/Ti suffixes.

#### mutator.py

Applies placement constraints to Kubernetes deployments via patch operations.

**Class: `PlacementMutator(namespace)`**

| Method | Purpose |
|---|---|
| `get_nodes()` | Queries all cluster nodes with resource info and taints |
| `get_deployments()` | Lists deployments with aggregated resource requests |
| `apply_strategy(strategy, target_node=None, seed=None)` | Computes + applies strategy, waits for rollout |
| `clear_placement()` | Removes nodeSelector from all managed deployments |
| `get_current_placement()` | Returns per-deployment placement state |

**Mechanism**: Patches `spec.template.spec.nodeSelector` with `kubernetes.io/hostname: <node>`. Tracks managed deployments via `chaosprobe.io/placement-strategy` annotation.

---

### 2.6 Output (`chaosprobe/output/`)

#### generator.py

**Class: `OutputGenerator(scenario, results, metrics=None, placement=None, service_routes=None)`**

| Method | Purpose |
|---|---|
| `generate()` | Full output: scenario files, infrastructure, experiments, summary, metrics. Returns output dict (persistence is done externally). |

**Schema version**: `2.0.0`. Top-level keys: `schemaVersion`, `runId`, `timestamp`, `scenario`, `infrastructure`, `experiments`, `summary`, `metrics` (optional), `loadGeneration` (optional), `anomalyLabels` (optional), `cascadeTimeline` (optional).

#### comparison.py

**Function: `compare_runs(baseline, after_fix, improvement_criteria=None) -> Dict`**

Compares two experiment runs and evaluates fix effectiveness.

**Effectiveness logic**:
- `fixEffective = True` if verdict FAIL->PASS, or score change >= 20, or all criteria met
- Confidence: base 0.5, +0.25 if verdict changed, +min(0.15, score_change/100), +0.10 if all experiments improved

#### visualize.py

Generates charts correlating placement strategies with performance metrics. Requires `matplotlib`.

| Function | Purpose |
|---|---|
| `generate_all_charts(store, output_dir, scenario=None)` | Generate all charts from database runs |
| `generate_from_dict(summary, output_dir)` | Generate charts from an in-memory summary dict |
| `generate_from_summary(summary_path, output_dir)` | Generate charts from a legacy summary.json file |
| `_chart_resilience_scores(strategies, output_path, iteration_data=None)` | Bar chart of resilience scores per strategy |
| `_chart_recovery_times(strategies, output_path, iteration_data=None)` | Mean/p95 recovery time comparison |
| `_chart_load_metrics(strategies, output_path)` | p95 latency and error rate overlay |
| `_chart_pod_node_heatmap(store, runs, output_path)` | Pod-to-node placement heatmap |
| `_chart_latency_by_strategy(latency_by_strategy, output_path)` | Inter-service latency comparison per strategy |
| `_chart_latency_degradation(latency_by_strategy, output_path)` | Latency degradation during chaos |
| `_chart_throughput_by_strategy(throughput_by_strategy, output_path)` | Throughput comparison per strategy |
| `_chart_throughput_degradation(throughput_by_strategy, output_path)` | Throughput degradation during chaos |
| `_chart_resource_utilization(resource_by_strategy, output_path)` | Resource utilization comparison |
| `_chart_resource_by_phase(resource_by_strategy, output_path)` | Resource usage by experiment phase |
| `_chart_prometheus_by_phase(prometheus_by_strategy, output_path)` | Prometheus metrics by experiment phase |
| `_generate_html_summary(chart_paths, strategies, output_path, iterations=1, latency_data=None, throughput_data=None, resource_data=None, prometheus_data=None)` | HTML report combining all charts |

---

### 2.7 Load Generation (`chaosprobe/loadgen/`)

#### runner.py

Locust-based load generator with preset profiles and CSV stats parsing.

**Dataclass: `LoadProfile(name, users, spawn_rate, duration_seconds)`**

| Profile | Users | Spawn Rate | Duration |
|---|---|---|---|
| `steady` | 50 | 10/s | 120s |
| `ramp` | 100 | 5/s | 180s |
| `spike` | 200 | 50/s | 90s |

**Dataclass: `LoadStats`** — Collected statistics: total requests/failures, avg/min/max/p50/p95/p99 response times, RPS, error rate, per-endpoint breakdown.

**Class: `LocustRunner(target_url, locustfile=None)`**

| Method | Purpose |
|---|---|
| `start(profile)` | Start headless Locust with the given profile |
| `stop()` | Terminate the Locust process |
| `wait()` | Wait for Locust to complete |
| `collect_stats()` | Parse CSV output into `LoadStats` |
| `cleanup()` | Remove temporary directories |

Supports context manager protocol (`with LocustRunner(...) as runner:`).

**Default locustfile**: Simulates web application browsing (index, browse, cart, checkout). The default user class is `FrontendUser`.

---

### 2.8 Storage (`chaosprobe/storage/`)

#### base.py

**Abstract class: `ResultStore`** — supports context manager protocol (`with store:`)

| Method | Purpose |
|---|---|
| `save_run(run_data)` | Persist a complete run result |
| `get_run(run_id)` | Retrieve a run by ID |
| `list_runs(scenario, strategy, limit)` | List runs with optional filters |
| `get_metrics(run_id, metric_name)` | Get metrics for a run |
| `compare_strategies(scenario, limit_per_strategy)` | Compare strategies across runs |
| `export_csv(output_path)` | Export all runs to CSV |
| `get_metric_trend(metric_name, strategy, limit)` | Get historical trend of a metric |
| `get_metric_names()` | Return all distinct metric names |
| `get_runs_below_threshold(metric_name, threshold, strategy)` | Find runs below a threshold |
| `close()` | Release resources |

#### sqlite.py

**Class: `SQLiteStore(db_path=None)`**

SQLite-based implementation of `ResultStore`. Default path: `~/.chaosprobe/results.db`.

**Tables**:
| Table | Purpose |
|---|---|
| `runs` | Run metadata, verdict, resilience score, raw JSON |
| `metrics` | Per-run metrics (recovery times, etc.) |
| `pod_placements` | Pod-to-node assignments per run |
| `load_stats` | Locust load generation statistics per run |

Uses WAL journal mode and foreign keys. Schema version: 1.

---

### 2.9 Infrastructure (`chaosprobe/provisioner/`)

#### kubernetes.py

**Class: `KubernetesProvisioner(namespace)`**

Applies standard K8s manifests from scenarios. Supports: Deployment, Service, ConfigMap, NetworkPolicy, PodDisruptionBudget, Secret, DaemonSet, StatefulSet, and generic kinds.

| Method | Purpose |
|---|---|
| `provision(manifests)` | Ensures namespace, applies all manifests, waits for readiness |
| `cleanup()` | Deletes all applied resources in reverse order |
| `cleanup_namespace()` | Deletes entire namespace |

#### setup.py (1501 lines)

**Class: `LitmusSetup`**

Handles all infrastructure bootstrapping.

| Capability | Methods |
|---|---|
| Prerequisites | `check_prerequisites()` -> checks kubectl, helm, git, ssh, ansible, cluster_access |
| LitmusChaos | `ensure_helm()`, `install_litmus()`, `setup_rbac()`, `install_experiment()` |
| ChaosCenter API | `chaoscenter_save_experiment(...)`, `chaoscenter_run_experiment(...)` |
| Vagrant | `create_vagrantfile()`, `vagrant_up()`, `vagrant_deploy_cluster()`, `vagrant_status()`, `vagrant_destroy()`, `vagrant_fetch_kubeconfig()` |
| Kubespray | `deploy_cluster()`, `generate_inventory()`, `get_kubeconfig()` |

**ChaosCenter API methods** (used by `ChaosRunner._register_with_chaoscenter`):
- `chaoscenter_save_experiment(gql_url, project_id, token, infra_id, experiment_id, name, manifest)` — calls `saveChaosExperiment` GraphQL mutation
- `chaoscenter_run_experiment(gql_url, project_id, token, experiment_id)` — calls `runChaosExperiment` GraphQL mutation, returns `notifyID`

**Defaults**: Vagrant box `generic/ubuntu2204`, 2 CPUs, 4096MB RAM per VM, Kubespray v2.24.0.

---

## 3. CLI Commands

### Core

| Command | Purpose |
|---|---|
| `chaosprobe init` | Install LitmusChaos, setup RBAC |
| `chaosprobe status [--json]` | Check prerequisites and cluster connectivity |
| `chaosprobe run [-n namespace]` | Run placement experiment matrix (all defaults: steady load, db, viz) |
| `chaosprobe provision <scenario>` | Deploy manifests only (no experiments) |
| `chaosprobe compare run-id-1 run-id-2 --neo4j-uri bolt://localhost:7687` | Compare before/after runs |
| `chaosprobe cleanup <namespace> [--all]` | Remove experiments and optionally namespace |

### Placement

| Command | Purpose |
|---|---|
| `chaosprobe placement apply <strategy> -n <ns>` | Apply placement strategy (colocate/spread/random/antagonistic) |
| `chaosprobe placement show -n <ns>` | Display current pod placement |
| `chaosprobe placement nodes` | List cluster nodes with resources |
| `chaosprobe placement clear -n <ns>` | Remove all placement constraints |

### Run (Placement Experiment Matrix)

```
chaosprobe run [options]
```

| Option | Default | Purpose |
|---|---|---|
| `-n, --namespace` | `online-boutique` | Target namespace |
| `-o, --output-dir` | `results` | Base results directory (timestamped subdir created) |
| `-s, --strategies` | all 5 | Comma-separated subset |
| `-i, --iterations` | 1 | Iterations per strategy |
| `-e, --experiment` | `scenarios/online-boutique/placement-experiment.yaml` | Custom experiment YAML |
| `-t, --timeout` | 300 | Engine timeout (seconds) |
| `--seed` | 42 | Random strategy seed |
| `--settle-time` | 30 | Wait between placement and experiment |
| `--provision` | off | Auto-provision cluster from scenario cluster.yaml |
| `--load-profile` | `steady` | Locust load profile (steady/ramp/spike) |
| `--locustfile` | built-in | Custom locustfile path |
| `--target-url` | `http://frontend.online-boutique.svc.cluster.local` | URL for Locust load generation |
| `--db` | `results.db` | SQLite database path for persistence |
| `--visualize/--no-visualize` | on | Generate charts after run |
| `--measure-latency/--no-measure-latency` | on | Measure inter-service latency |
| `--measure-redis/--no-measure-redis` | on | Measure Redis throughput |
| `--measure-disk/--no-measure-disk` | on | Measure disk I/O throughput |
| `--measure-resources/--no-measure-resources` | on | Measure node/pod resource utilization |
| `--collect-logs/--no-collect-logs` | on | Collect container logs from target deployment |
| `--measure-prometheus/--no-measure-prometheus` | on | Query Prometheus for cluster metrics |
| `--prometheus-url` | auto-discovered | Prometheus server URL(s); repeat for multiple |
| `--baseline-duration` | 0 | Seconds to collect steady-state metrics before chaos |
| `--neo4j-uri` | `bolt://localhost:7687` | Neo4j connection URI (env: `NEO4J_URI`) |
| `--neo4j-user` | `neo4j` | Neo4j username (env: `NEO4J_USER`) |
| `--neo4j-password` | `chaosprobe` | Neo4j password (env: `NEO4J_PASSWORD`) |
| `--no-auto-setup` | off | Disable automatic LitmusChaos installation |

**Workflow per strategy**: apply placement -> settle -> start RecoveryWatcher -> start Locust -> run experiment -> stop Locust/watcher -> collect results + metrics -> clear placement -> next strategy.

### Query (Database)

| Command | Purpose |
|---|---|
| `chaosprobe query runs [--db path]` | List stored runs |
| `chaosprobe query compare [--db path]` | Compare strategies across runs |
| `chaosprobe query show <run-id> [--db path]` | Show details of a specific run |
| `chaosprobe query export [--db path] -o file.csv` | Export all runs to CSV |

### Visualize

| Command | Purpose |
|---|---|
| `chaosprobe visualize --neo4j-uri <uri> --session <id> -o <dir>` | Generate charts from Neo4j session |
| `chaosprobe visualize --db <path> -o <dir>` | Generate charts from SQLite database |
| `chaosprobe visualize --summary <file> -o <dir>` | Generate charts from summary file (legacy) |

Additional options: `--scenario` (filter by scenario in DB mode), `--neo4j-user`, `--neo4j-password`.

Generated charts: resilience score bars, recovery time comparison, load metrics overlay, pod-node heatmap, HTML summary report.

### Graph (Neo4j)

| Command | Purpose |
|---|---|
| `chaosprobe graph status` | Check Neo4j connectivity and show node counts |
| `chaosprobe graph sessions` | List all experiment sessions stored in Neo4j |
| `chaosprobe graph blast-radius <service> [--max-hops N]` | Show upstream dependents affected by a service failure |
| `chaosprobe graph topology --run-id <id>` | Show pod-to-node placement topology for a run |
| `chaosprobe graph details <run-id> [--json]` | Show comprehensive data for a single run |
| `chaosprobe graph compare --run-ids <id1,id2,...>` | Compare strategies across specified runs |

All graph commands accept `--neo4j-uri`, `--neo4j-user`, `--neo4j-password`.

### ML Export

| Command | Purpose |
|---|---|
| `chaosprobe ml-export --neo4j-uri <uri> -o <file>` | Export aligned time-series dataset from Neo4j |
| `chaosprobe ml-export --db <path> -o <file>` | Export from SQLite |

Produces CSV (default) or Parquet (`--format parquet`, requires `pyarrow[parquet]`) with aligned features and anomaly labels.

### Cluster Management

| Command | Purpose |
|---|---|
| `chaosprobe cluster vagrant init` | Generate Vagrantfile for multi-node cluster |
| `chaosprobe cluster vagrant up [--provider]` | Start VMs (virtualbox or libvirt) |
| `chaosprobe cluster vagrant deploy` | Deploy K8s via Kubespray on VMs |
| `chaosprobe cluster vagrant kubeconfig` | Fetch kubeconfig from control plane |
| `chaosprobe cluster vagrant status` | Check VM and cluster health |
| `chaosprobe cluster vagrant ssh <vm>` | SSH into a VM |
| `chaosprobe cluster vagrant destroy` | Tear down VMs |
| `chaosprobe cluster create --hosts-file` | Production cluster via Kubespray |
| `chaosprobe cluster kubeconfig --host <ip>` | Fetch kubeconfig from remote host |

---

## 4. Data Flow: run Command

```
1. Load shared scenario (placement-experiment.yaml)
2. Parse service topology from scenario manifests (config/topology.py)
3. Extract target deployment from ChaosEngine appinfo
4. Create MetricsCollector for namespace
5. Open shared SQLiteStore (results.db by default)

For each strategy in [baseline, colocate, spread, antagonistic, random]:
    6. Apply placement via PlacementMutator
    7. Wait settle-time (30s default)

    For each iteration (1..N):
        8. Start RecoveryWatcher(namespace, target_deployment)
        9. Start LocustRunner with load profile (steady by default)
        10. Record experiment_start = time.time()
        11. ChaosRunner.run_experiments() -- registers with ChaosCenter (if configured),
            then creates ChaosEngine CRD and blocks until engine completes
        12. Record experiment_end = time.time()
        13. Stop LocustRunner, collect LoadStats, cleanup temp dirs
        14. RecoveryWatcher.stop()
        15. ResultCollector.collect() -- ChaosResult CRDs
        16. MetricsCollector.collect(recovery_data=watcher.result())
        17. OutputGenerator.generate() -- build output_data dict
        18. Neo4jStore.sync_run(output_data) -- sync to graph database
        19. SQLiteStore.save_run(output_data) -- persist to SQLite

    20. Clear placement constraints
    21. Wait for rollout

22. Build comparison table + remediation log
23. Close SQLiteStore and Neo4jStore
24. Generate visualization charts (on by default)
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
    }, {
      "name": "checkout-pod-ready",
      "type": "k8sProbe",
      "mode": "Continuous",
      "status": {"verdict": "Failed", "description": "no resource found..."}
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
        "deletionTime": "2026-02-27T13:39:04+00:00",
        "scheduledTime": "2026-02-27T13:39:05+00:00",
        "readyTime": "2026-02-27T13:39:06+00:00",
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
    },
    "podStatus": {"pods": [...], "totalRestarts": 0},
    "eventTimeline": [...],
    "nodeInfo": {"nodeName": "worker1", "allocatable": {"cpu": "2", "memory": "1908500Ki"}, "capacity": {"cpu": "2", "memory": "2010900Ki"}}
  }
}
```

### Summary (in-memory, synced to Neo4j)

```json
{
  "runId": "run-20260227-131031",
  "timestamp": "...",
  "namespace": "online-boutique",
  "iterations": 1,
  "strategies": {
    "<name>": {
      "strategy": "colocate",
      "status": "completed",
      "placement": {"strategy": "...", "assignments": {...}, "metadata": {...}},
      "experiment": {"totalExperiments": 1, "passed": 0, "failed": 1, "resilienceScore": 50.0, "overallVerdict": "FAIL"},
      "metrics": {...},
      "runId": "run-2026-04-02-131031-abc123"
    }
  },
  "summary": {"totalStrategies": 5, "passed": 0, "failed": 5},
  "comparison": [
    {"strategy": "colocate", "verdict": "FAIL", "resilienceScore": 50.0, "avgRecovery_ms": 4140, "maxRecovery_ms": 18341}
  ]
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

**Per-experiment verdict**: determined by LitmusChaos probe evaluation. `Pass` if all probes meet their criteria, `Fail` if any probe fails.

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

| Probe | Type | Purpose | Expected |
|---|---|---|---|
| `frontend-availability` | httpProbe (Continuous, 1s interval) | Frontend returns HTTP 200 | Always passes (frontend is resilient) |
| `checkout-pod-ready` | k8sProbe (Continuous, 1s interval) | Running checkoutservice pod exists | Fails during deletion gaps |

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
    cli.py                   # CLI entry point (~3200 lines)
    config/
      loader.py              # Scenario loading
      topology.py            # Dynamic service dependency extraction
      validator.py           # Validation
    chaos/
      runner.py              # ChaosEngine execution
    collector/
      result_collector.py    # ChaosResult collection
    loadgen/
      runner.py              # Locust load generation
    metrics/
      recovery.py            # Real-time pod watch
      collector.py           # Metrics aggregation
      latency.py             # Continuous latency prober
      throughput.py          # Redis/disk throughput probers
      resources.py           # Resource usage prober
      prometheus.py          # Prometheus metrics prober
      anomaly_labels.py      # Ground-truth ML labels
      cascade.py             # Fault propagation tracking
      remediation.py         # Remediation action logs
      timeseries.py          # Time-series alignment
    output/
      generator.py           # Structured output generation
      comparison.py          # Run comparison
      visualize.py           # Charts & HTML reports
      ml_export.py           # ML-ready dataset export
    placement/
      strategy.py            # Placement strategies
      mutator.py             # K8s patch operations
    provisioner/
      kubernetes.py          # Manifest application
      setup.py               # LitmusChaos/Neo4j/Vagrant/Kubespray
    storage/
      base.py                # ResultStore ABC
      sqlite.py              # SQLite backend
      neo4j_store.py         # Neo4j graph store (primary)
    graph/
      analysis.py            # High-level graph analysis functions
  scenarios/
    online-boutique/
      deploy/                # 12 microservice manifests
      placement-experiment.yaml
      contention-*/          # CPU, memory, IO, network, Redis variants
    examples/
      nginx-pod-delete/      # Simple example scenario
  tests/
    test_config.py
    test_placement.py
    test_output.py
    test_loadgen.py
    test_storage.py
    test_visualize.py
    test_neo4j_store.py
    test_ml_export.py
    test_latency.py
    test_throughput.py
    test_resources.py
    test_prometheus.py
    test_collector.py
    test_timeseries.py
    test_cascade.py
    test_anomaly_labels.py
    test_remediation.py
    test_topology.py
  pyproject.toml
  README.md
  TECHNICAL.md               # This document
```

---

## 9. Dependencies

### Runtime
- `kubernetes` (>=28.0.0) - Official Python K8s client
- `click` (>=8.0.0) - CLI framework
- `pyyaml` (>=6.0) - YAML parsing
- `locust` (>=2.20.0) - Load generation
- `matplotlib` (>=3.7.0) - Chart generation and visualization
- `neo4j` (>=5.0.0) - Neo4j graph database driver (optional extra: `graph`)

### Development
- `pytest`, `pytest-cov` - Testing
- `black`, `ruff` - Formatting/linting
- `mypy` - Type checking

### External Services
- Kubernetes cluster (any version with CRD support)
- LitmusChaos (auto-installed via Helm)
- Vagrant + libvirt/VirtualBox (local dev only)
