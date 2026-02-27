# ChaosProbe Technical Reference

## 1. System Overview

ChaosProbe is a Python framework for automated Kubernetes chaos testing with AI-consumable output. It wraps LitmusChaos to run native ChaosEngine experiments, collects real-time pod recovery metrics, and produces structured JSON reports for machine-learning feedback loops.

**Core loop**: deploy manifests -> run chaos experiments -> collect metrics -> generate structured output -> AI reads output, edits manifests, re-runs, compares.

```
ChaosProbe CLI (cli.py, 1824 lines)
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
      |     Auto-classifies YAML files by kind field
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
      +-- Metrics Collection
      |     +-- RecoveryWatcher (metrics/recovery.py)
      |     |   Real-time pod watch during chaos
      |     +-- MetricsCollector (metrics/collector.py)
      |         Pod status, node info, unified output
      |
      +-- Result Collector (collector/result_collector.py)
      |     ChaosResult CRDs, probe verdicts, resilience score
      |
      +-- Output Generator (output/generator.py)
            +-- Comparison Engine (output/comparison.py)
```

---

## 2. Module Reference

### 2.1 Configuration (`chaosprobe/config/`)

#### loader.py

Loads scenario directories or single YAML files. Auto-classifies resources by their `kind` field: ChaosEngine kinds go to `experiments`, everything else to `manifests`.

| Function | Signature | Purpose |
|---|---|---|
| `load_scenario` | `(scenario_path: str) -> Dict` | Main entry point. Returns `{path, manifests, experiments, namespace}` |
| `_load_yaml_file` | `(filepath: Path) -> Tuple[List, List]` | Parses multi-document YAML, classifies by kind |
| `_load_yaml_directory` | `(dirpath: Path) -> Tuple[List, List]` | Loads all .yaml/.yml from directory |
| `_detect_namespace` | `(experiments: List) -> str` | Extracts namespace from ChaosEngine appinfo. Default: `"default"` |
| `merge_configs` | `(*configs) -> Dict` | Deep-merges configuration dictionaries |

**Constants**: `CHAOS_KINDS = {"ChaosEngine"}`

#### validator.py

Validates loaded scenarios for structural correctness before execution.

| Function | Purpose |
|---|---|
| `validate_scenario(scenario)` | Validates entire scenario. Raises `ValidationError` with aggregated errors |
| `_validate_chaos_engine(spec, filepath)` | Checks: apiVersion, kind, experiments list, applabel, chaosServiceAccount |
| `_validate_manifest(spec, filepath)` | Checks: apiVersion, kind, metadata.name |

---

### 2.2 Chaos Execution (`chaosprobe/chaos/`)

#### runner.py

Orchestrates ChaosEngine lifecycle: create, poll, collect status, cleanup.

**Class: `ChaosRunner(namespace, timeout=300)`**

| Method | Purpose |
|---|---|
| `run_experiments(experiments)` | Runs all ChaosEngine experiments sequentially |
| `_run_single_experiment(engine_spec)` | Patches spec with unique suffix, creates CRD, waits for completion |
| `_wait_for_engine(engine_name, start_time)` | Polls engine status every 5s until completed/timeout |
| `_delete_chaos_engine(engine_name)` | Idempotent delete with finalizer cleanup |
| `get_executed_experiments()` | Returns metadata for all executed experiments |

**Kubernetes API**: Uses `CustomObjectsApi` for ChaosEngine CRUD on `litmuschaos.io/v1alpha1`.

---

### 2.3 Result Collection (`chaosprobe/collector/`)

#### result_collector.py

Collects ChaosResult CRDs and calculates resilience metrics.

**Class: `ResultCollector(namespace)`**

| Method | Purpose |
|---|---|
| `collect(executed_experiments)` | Collects results for all executed experiments |
| `_collect_experiment_result(engine_name, exp_name)` | Gathers engine status, ChaosResult, verdict, probe success |
| `_get_chaos_result(engine_name, exp_name)` | Tries multiple naming patterns for ChaosResult lookup |
| `_parse_chaos_result(chaos_result)` | Extracts phase, verdict, probe success %, probe statuses |
| `_determine_verdict(result)` | Returns "Pass", "Fail", or "Awaited" |

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
| `collect(deployment_name, since_time, until_time, recovery_data=None)` | Unified metrics with recovery, pod status, node info |
| `_collect_pod_status(deployment_name)` | Current pod phases, restart counts, conditions |
| `_collect_node_info(deployment_name)` | Node allocatable/capacity for CPU and memory |

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
| `apply_assignment(assignment)` | Applies pre-computed NodeAssignment |
| `clear_placement()` | Removes nodeSelector from all managed deployments |
| `get_current_placement()` | Returns per-deployment placement state |

**Mechanism**: Patches `spec.template.spec.nodeSelector` with `kubernetes.io/hostname: <node>`. Tracks managed deployments via `chaosprobe.io/placement-strategy` annotation.

---

### 2.6 Output (`chaosprobe/output/`)

#### generator.py

**Class: `OutputGenerator(scenario, results, metrics=None)`**

| Method | Purpose |
|---|---|
| `generate()` | Full output: scenario files, infrastructure, experiments, summary, metrics |
| `generate_minimal()` | Quick format: runId, verdict, resilienceScore, issueDetected |

**Schema version**: `2.0.0`. Top-level keys: `schemaVersion`, `runId`, `timestamp`, `scenario`, `infrastructure`, `experiments`, `summary`, `metrics` (optional).

#### comparison.py

**Function: `compare_runs(baseline, after_fix, improvement_criteria=None) -> Dict`**

Compares two experiment runs and evaluates fix effectiveness.

**Effectiveness logic**:
- `fixEffective = True` if verdict FAIL->PASS, or score change >= 20, or all criteria met
- Confidence: base 0.5, +0.25 if verdict changed, +min(0.15, score_change/100), +0.10 if all experiments improved

---

### 2.7 Infrastructure (`chaosprobe/provisioner/`)

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
| Vagrant | `vagrant_init()`, `vagrant_up()`, `vagrant_deploy()`, `vagrant_status()`, `vagrant_ssh()`, `vagrant_destroy()`, `vagrant_kubeconfig()` |
| Kubespray | `cluster_create()`, `cluster_destroy()`, `get_kubeconfig()` |

**Defaults**: Vagrant box `generic/ubuntu2204`, 2 CPUs, 2048MB RAM per VM, Kubespray v2.24.0.

---

## 3. CLI Commands

### Core

| Command | Purpose |
|---|---|
| `chaosprobe init` | Install LitmusChaos, setup RBAC |
| `chaosprobe status [--json]` | Check prerequisites and cluster connectivity |
| `chaosprobe run <scenario> -o results.json` | Run a chaos scenario and generate output |
| `chaosprobe provision <scenario>` | Deploy manifests only (no experiments) |
| `chaosprobe compare baseline.json after.json -o comparison.json` | Compare before/after runs |
| `chaosprobe cleanup <namespace> [--all]` | Remove experiments and optionally namespace |

### Placement

| Command | Purpose |
|---|---|
| `chaosprobe placement apply <strategy> -n <ns>` | Apply placement strategy (colocate/spread/random/antagonistic) |
| `chaosprobe placement show -n <ns>` | Display current pod placement |
| `chaosprobe placement nodes` | List cluster nodes with resources |
| `chaosprobe placement clear -n <ns>` | Remove all placement constraints |

### Run-All (Placement Experiment Matrix)

```
chaosprobe run-all -n <namespace> [options]
```

| Option | Default | Purpose |
|---|---|---|
| `-n, --namespace` | required | Target namespace |
| `-o, --output-dir` | `results/<timestamp>` | Results directory |
| `-s, --strategies` | all 5 | Comma-separated subset |
| `-i, --iterations` | 1 | Iterations per strategy |
| `-e, --experiment` | auto-detected | Custom experiment YAML |
| `-t, --timeout` | 300 | Engine timeout (seconds) |
| `--seed` | None | Random strategy seed |
| `--settle-time` | 30 | Wait between placement and experiment |

**Workflow per strategy**: apply placement -> settle -> start RecoveryWatcher -> run experiment -> stop watcher -> collect results + metrics -> clear placement -> next strategy.

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

## 4. Data Flow: run-all Command

```
1. Load shared scenario (placement-experiment.yaml)
2. Extract target deployment from ChaosEngine appinfo
3. Create MetricsCollector for namespace

For each strategy in [baseline, colocate, spread, antagonistic, random]:
    4. Apply placement via PlacementMutator
    5. Wait settle-time (30s default)

    For each iteration (1..N):
        6. Start RecoveryWatcher(namespace, target_deployment)
        7. Record experiment_start = time.time()
        8. ChaosRunner.run_experiments() -- blocks until engine completes
        9. Record experiment_end = time.time()
        10. RecoveryWatcher.stop()
        11. ResultCollector.collect() -- ChaosResult CRDs
        12. MetricsCollector.collect(recovery_data=watcher.result())
        13. OutputGenerator.generate() -- write {strategy}.json

    14. Clear placement constraints
    15. Wait for rollout

16. Write summary.json with comparison table
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

### Summary (run-all)

```json
{
  "runId": "run-all-20260227-131031",
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
      "resultFile": "results/.../colocate.json"
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
    cli.py                   # CLI entry point (1824 lines)
    config/
      loader.py              # Scenario loading (140 lines)
      validator.py           # Validation (102 lines)
    chaos/
      runner.py              # ChaosEngine execution (234 lines)
    collector/
      result_collector.py    # ChaosResult collection (243 lines)
    metrics/
      recovery.py            # Real-time pod watch (255 lines)
      collector.py           # Metrics aggregation (170 lines)
    output/
      generator.py           # JSON output (145 lines)
      comparison.py          # Run comparison (266 lines)
    placement/
      strategy.py            # Placement strategies (324 lines)
      mutator.py             # K8s patch operations (461 lines)
    provisioner/
      kubernetes.py          # Manifest application (266 lines)
      setup.py               # LitmusChaos/Vagrant/Kubespray (1501 lines)
  scenarios/
    online-boutique/
      deploy/                # 12 microservice manifests
      placement-experiment.yaml
      experiment-variants/   # CPU, memory, IO, network, Redis variants
    examples/
      nginx-pod-delete/      # Simple example scenario
  tests/
    test_config.py           # 10 tests
    test_placement.py        # 40 tests
    test_output.py           # 12 tests
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

### Development
- `pytest`, `pytest-cov` - Testing
- `black`, `ruff` - Formatting/linting
- `mypy` - Type checking

### External Services
- Kubernetes cluster (any version with CRD support)
- LitmusChaos (auto-installed via Helm)
- Vagrant + libvirt/VirtualBox (local dev only)
