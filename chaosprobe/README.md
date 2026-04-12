# ChaosProbe

A framework for running LitmusChaos experiments against Kubernetes deployments, collecting structured experiment data into Neo4j for AI-driven anomaly classification and remediation.

## Overview

ChaosProbe enables automated chaos testing with an AI feedback loop:

1. **Cluster Deployment**: Deploy Kubernetes clusters via Vagrant (local) or Kubespray (production)
2. **Auto-Setup**: Installs Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, and Neo4j
3. **Deploy Manifests**: Applies standard K8s manifests to the cluster
4. **Run Experiments**: Executes ChaosEngine experiments via the ChaosCenter GraphQL API across placement strategies
5. **Collect to Neo4j**: Stores results, metrics, anomaly labels, and time-series in a Neo4j graph database
6. **ML Export**: Exports aligned, labeled datasets for anomaly classification and remediation models
7. **Compare Runs**: Diffs before/after results to evaluate fix effectiveness

### AI Feedback Loop

```
AI reads output → edits K8s manifests → re-runs ChaosProbe → compares results → repeats
```

## Installation

```bash
cd chaosprobe
uv sync          # creates .venv, installs all dependencies
```

## Prerequisites

- `kubectl`
- Python 3.9+
- [uv](https://docs.astral.sh/uv/) package manager

> Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, and Neo4j are automatically installed by `chaosprobe init`.

### For Local Development (Vagrant)

- [Vagrant](https://www.vagrantup.com/downloads)
- VirtualBox or libvirt provider
- `git`, Python 3 with `venv` module

### For Production Deployment (Kubespray)

- `git`, `ssh`, Python 3 with `venv` module (`apt install python3-venv`)

## Scenario Format

Scenarios are **directories** containing standard Kubernetes manifests and ChaosEngine YAML files. ChaosProbe auto-classifies files by their `kind` field.

```
scenarios/nginx-pod-delete/
  deployment.yaml     # Standard K8s Deployment
  service.yaml        # Standard K8s Service
  experiment.yaml     # ChaosEngine experiment
```

### Example: experiment.yaml (ChaosEngine)

```yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: nginx-pod-delete
spec:
  engineState: active
  appinfo:
    appns: chaosprobe-test
    applabel: app=nginx
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: "30"
            - name: CHAOS_INTERVAL
              value: "10"
        probe:
          - name: http-probe
            type: httpProbe
            mode: Continuous
            httpProbe/inputs:
              url: http://nginx-service.chaosprobe-test.svc.cluster.local
              method:
                get:
                  criteria: "=="
                  responseCode: "200"
            runProperties:
              probeTimeout: 5s
              interval: 2s
              retry: 3
```

### Optional: cluster.yaml

Scenarios can include a `cluster.yaml` to couple cluster provisioning with the experiment:

```yaml
provider: vagrant
workers:
  count: 3
  cpu: 2
  memory: 2048
  disk: "20GB"
```

When `--provision` is passed to `run`, this config provisions the cluster automatically.

## Quick Start

### With Existing Cluster

```bash
# Initialize (installs LitmusChaos, ChaosCenter, Prometheus, Neo4j, metrics-server)
uv run chaosprobe init

# Run the full placement experiment matrix
uv run chaosprobe run -n online-boutique

# Compare before and after (using Neo4j run IDs)
uv run chaosprobe compare run-baseline-001 run-afterfix-001 --neo4j-uri bolt://localhost:7687
```

### Local Development with Vagrant

```bash
# 1. Initialize Vagrantfile (1 control plane + 2 workers)
uv run chaosprobe cluster vagrant init --control-planes 1 --workers 2

# 2. (WSL2/Linux) Setup libvirt provider — run once
uv run chaosprobe cluster vagrant setup

# 3. Start the VMs
uv run chaosprobe cluster vagrant up                      # VirtualBox (default)
uv run chaosprobe cluster vagrant up --provider libvirt   # WSL2/Linux

# 4. Deploy Kubernetes (takes 15-30 minutes)
uv run chaosprobe cluster vagrant deploy

# 5. Fetch kubeconfig
uv run chaosprobe cluster vagrant kubeconfig
export KUBECONFIG=~/.kube/config-chaosprobe

# 6. Initialize ChaosProbe and run
uv run chaosprobe init
uv run chaosprobe run -n online-boutique

# Destroy VMs when done
uv run chaosprobe cluster vagrant destroy
```

### Deploy on Bare Metal / Cloud VMs (Kubespray)

```bash
# 1. Create a hosts file
cat > hosts.yaml << EOF
hosts:
  - name: master1
    ip: 192.168.1.10
    ansible_user: ubuntu
    roles: [control_plane, worker]
  - name: worker1
    ip: 192.168.1.11
    ansible_user: ubuntu
    roles: [worker]
  - name: worker2
    ip: 192.168.1.12
    ansible_user: ubuntu
    roles: [worker]
EOF

# 2. Deploy cluster (15-30 minutes)
uv run chaosprobe cluster create --hosts-file hosts.yaml

# 3. Fetch kubeconfig
uv run chaosprobe cluster kubeconfig --host 192.168.1.10 --user ubuntu
export KUBECONFIG=~/.kube/config-chaosprobe

# 4. Run scenarios
uv run chaosprobe init
uv run chaosprobe run -n online-boutique
```

## Commands

### Core

```bash
uv run chaosprobe status                    # Check prerequisites and cluster connectivity
uv run chaosprobe init                      # Install all infrastructure
uv run chaosprobe run -n online-boutique    # Run placement experiment matrix
uv run chaosprobe provision <scenario-dir>  # Deploy manifests only (no experiments)
uv run chaosprobe compare <base> <fix> --neo4j-uri bolt://localhost:7687  # Compare runs
uv run chaosprobe cleanup <namespace> --all # Cleanup provisioned resources
uv run chaosprobe delete -n <namespace>     # Delete ALL ChaosProbe infrastructure
```

### Run

The `run` command runs the full placement experiment matrix. Default strategies: `baseline,colocate,spread,antagonistic,random`.

```bash
uv run chaosprobe run -n online-boutique                          # All defaults
uv run chaosprobe run -n online-boutique -s colocate,spread       # Specific strategies
uv run chaosprobe run -n online-boutique -i 3                     # Multiple iterations
uv run chaosprobe run -n online-boutique --load-profile ramp      # Ramp load profile
uv run chaosprobe run -n online-boutique --provision              # Auto-provision cluster
uv run chaosprobe run -n online-boutique --no-visualize           # Skip chart generation
```

### Placement

```bash
uv run chaosprobe placement apply colocate -n online-boutique
uv run chaosprobe placement apply spread -n online-boutique
uv run chaosprobe placement apply random -n online-boutique --seed 42
uv run chaosprobe placement apply antagonistic -n online-boutique
uv run chaosprobe placement show -n online-boutique
uv run chaosprobe placement nodes
uv run chaosprobe placement clear -n online-boutique
```

### Graph (Neo4j)

```bash
uv run chaosprobe graph status --neo4j-uri bolt://localhost:7687
uv run chaosprobe graph sessions --neo4j-uri bolt://localhost:7687
uv run chaosprobe graph blast-radius frontend --neo4j-uri bolt://localhost:7687
uv run chaosprobe graph topology --run-id <run-id> --neo4j-uri bolt://localhost:7687
uv run chaosprobe graph details <run-id> --neo4j-uri bolt://localhost:7687
uv run chaosprobe graph compare --run-ids <id1,id2,...> --neo4j-uri bolt://localhost:7687
```

### Dashboard (ChaosCenter)

```bash
uv run chaosprobe dashboard install
uv run chaosprobe dashboard status
uv run chaosprobe dashboard open
uv run chaosprobe dashboard connect -n <namespace>
uv run chaosprobe dashboard credentials
```

### Visualization

```bash
uv run chaosprobe visualize --neo4j-uri bolt://localhost:7687 -o charts/
uv run chaosprobe visualize --neo4j-uri bolt://localhost:7687 --session <id> -o charts/
uv run chaosprobe visualize --summary summary.json -o charts/    # Legacy
```

### ML Export

```bash
uv run chaosprobe ml-export --neo4j-uri bolt://localhost:7687 -o dataset.csv
uv run chaosprobe ml-export --neo4j-uri bolt://localhost:7687 -o dataset.parquet --format parquet
uv run chaosprobe ml-export --neo4j-uri bolt://localhost:7687 --strategy colocate -o dataset.csv
```

### Probe (Rust cmdProbes)

```bash
uv run chaosprobe probe init --scenario <path>
uv run chaosprobe probe build --scenario <path>
uv run chaosprobe probe list --scenario <path>
```

### Cluster

```bash
# Vagrant
uv run chaosprobe cluster vagrant init
uv run chaosprobe cluster vagrant setup          # libvirt for WSL2/Linux
uv run chaosprobe cluster vagrant up
uv run chaosprobe cluster vagrant deploy
uv run chaosprobe cluster vagrant kubeconfig
uv run chaosprobe cluster vagrant status
uv run chaosprobe cluster vagrant ssh <vm-name>
uv run chaosprobe cluster vagrant destroy

# Kubespray
uv run chaosprobe cluster create --hosts-file hosts.yaml
uv run chaosprobe cluster kubeconfig --host <ip> --user <user>
uv run chaosprobe cluster destroy --inventory <path>
```

## Supported Chaos Experiments

Any LitmusChaos experiment can be used via ChaosEngine YAML. Common ones:

- **Pod**: `pod-delete`, `container-kill`, `pod-cpu-hog`, `pod-memory-hog`, `pod-io-stress`
- **Network**: `pod-network-loss`, `pod-network-latency`, `pod-network-corruption`
- **Node**: `node-cpu-hog`, `node-memory-hog`, `node-drain`

## Architecture

```
ChaosProbe CLI (cli.py + commands/)
      │
      ├── Cluster Manager
      │   ├── Vagrant (local development)
      │   └── Kubespray (production)
      │
      ├── Setup Manager (Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, Neo4j)
      │
      ├── Config Loader (directory-based, auto-classifies by kind)
      │   └── Validator (ChaosEngine + K8s manifest validation)
      │
      ├── Infrastructure Provisioner (applies raw K8s manifests)
      │
      ├── Placement Engine
      │   ├── Strategy (colocate, spread, random, antagonistic)
      │   └── Mutator (nodeSelector injection, rollout management)
      │
      ├── Chaos Runner (ChaosCenter GraphQL API — save, trigger, poll experiments)
      │
      ├── Result Collector (ChaosResult CRDs)
      │
      ├── Metrics Collection
      │   ├── RecoveryWatcher (real-time pod watch during chaos)
      │   ├── Continuous Probers (latency, throughput, resources, Prometheus)
      │   ├── Anomaly Labels (ground-truth ML labels)
      │   ├── Cascade Timeline (fault propagation tracking)
      │   └── MetricsCollector (pod status, node info, unified output)
      │
      ├── Storage — Neo4j Graph Store (topology, runs, metrics, time-series)
      │
      ├── Graph Analysis (blast radius, topology comparison, colocation impact)
      │
      └── Output
          ├── Visualization (charts, HTML reports)
          ├── ML Export (aligned CSV/Parquet datasets)
          └── Comparison Engine (diffs before/after runs)
```

## Development

```bash
uv sync                     # Sync all dependencies (including dev)
uv run pytest               # Run tests
uv run ruff check .         # Lint
uv run black --check .      # Check formatting
uv run black .              # Format code
```

## License

MIT
