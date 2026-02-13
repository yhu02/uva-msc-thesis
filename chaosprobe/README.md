# ChaosProbe

A framework for running native LitmusChaos experiments against Kubernetes deployments, producing structured AI-consumable output.

## Overview

ChaosProbe enables automated chaos testing with an AI feedback loop:

1. **Cluster Deployment**: Deploy Kubernetes clusters via Vagrant (local) or Kubespray (production)
2. **Auto-Setup**: Installs Helm and LitmusChaos automatically
3. **Deploy Manifests**: Applies standard K8s manifests to the cluster
4. **Run Experiments**: Executes native ChaosEngine experiments
5. **Generate AI Output**: Produces structured JSON with experiment results and resilience scores
6. **Compare Runs**: Diffs before/after results to evaluate fix effectiveness

### AI Feedback Loop

```
AI reads output → edits K8s manifests → re-runs ChaosProbe → compares results → repeats
```

The output contains experiment results and resilience scores so an AI agent can diagnose issues, edit manifests, re-run, and verify improvements.

## Installation

```bash
cd chaosprobe

# Sync dependencies and install (creates .venv automatically)
uv sync
```

## Prerequisites

- `kubectl`
- Python 3.9+
- [uv](https://docs.astral.sh/uv/) package manager

> **Note:** Helm and LitmusChaos are automatically installed if not present.

### For Local Development (Vagrant)

- [Vagrant](https://www.vagrantup.com/downloads)
- VirtualBox or libvirt provider
- `git`, Python 3 with `venv` module

### For Production Deployment (Kubespray)

- `git`, `ssh`, Python 3 with `venv` module (`apt install python3-venv`)

## Scenario Format

Scenarios are **directories** containing standard Kubernetes manifests and native ChaosEngine YAML files. ChaosProbe auto-classifies files by their `kind` field.

```
scenarios/nginx-pod-delete/
  deployment.yaml     # Standard K8s Deployment
  service.yaml        # Standard K8s Service
  experiment.yaml     # Native LitmusChaos ChaosEngine
```

### Example: deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx
  labels:
    app: nginx
spec:
  replicas: 1
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
        - name: nginx
          image: nginx:1.21
          ports:
            - containerPort: 80
```

### Example: experiment.yaml (Native ChaosEngine)

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

## Quick Start

### With Existing Cluster

```bash
# Initialize ChaosProbe (installs LitmusChaos)
uv run chaosprobe init

# Run a scenario directory
uv run chaosprobe run scenarios/examples/nginx-pod-delete/ -o results.json

# AI edits the deployment.yaml to fix issues, then re-run
uv run chaosprobe run scenarios/examples/nginx-pod-delete/ -o after-fix.json

# Compare before and after
uv run chaosprobe compare results.json after-fix.json -o comparison.json
```

### Local Development with Vagrant

```bash
# 1. Initialize Vagrantfile (1 control plane + 2 workers)
uv run chaosprobe cluster vagrant init --control-planes 1 --workers 2

# 2. (WSL2/Linux) Setup libvirt provider - run once
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
uv run chaosprobe run scenarios/examples/nginx-pod-delete/ -o results.json

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
uv run chaosprobe run scenarios/examples/nginx-pod-delete/ -o results.json
```

## Commands

### Core Commands

```bash
# Check status of all dependencies
uv run chaosprobe status

# Initialize (install LitmusChaos)
uv run chaosprobe init

# Run a scenario (directory or single file)
uv run chaosprobe run <scenario-dir> -o results.json

# Deploy manifests only (no experiments)
uv run chaosprobe provision <scenario-dir>

# Compare before/after results
uv run chaosprobe compare baseline.json after-fix.json -o comparison.json

# Cleanup resources
uv run chaosprobe cleanup <namespace> --all
```

### Cluster Commands

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

Any LitmusChaos experiment can be used via native ChaosEngine YAML. Common ones:

### Pod Chaos
- `pod-delete` — Delete application pods
- `container-kill` — Kill containers
- `pod-cpu-hog` — CPU stress
- `pod-memory-hog` — Memory stress
- `pod-io-stress` — I/O stress

### Network Chaos
- `pod-network-loss` — Packet loss
- `pod-network-latency` — Latency injection
- `pod-network-corruption` — Packet corruption

### Node Chaos
- `node-cpu-hog` — Node CPU stress
- `node-memory-hog` — Node memory stress
- `node-drain` — Node drain

## Output Format

ChaosProbe generates structured JSON (schema v2.0.0) for AI consumption:

```json
{
  "schemaVersion": "2.0.0",
  "runId": "run-2025-01-18-143052-abc123",
  "scenario": {
    "directory": "scenarios/nginx-pod-delete",
    "manifestFiles": ["deployment.yaml", "service.yaml"],
    "experimentFiles": ["experiment.yaml"]
  },
  "infrastructure": {
    "namespace": "chaosprobe-test",
    "resources": [
      { "file": "deployment.yaml", "kind": "Deployment", "name": "nginx" },
      { "file": "service.yaml", "kind": "Service", "name": "nginx-service" }
    ]
  },
  "experiments": [
    {
      "name": "pod-delete",
      "engineName": "nginx-pod-delete-a02e6e",
      "result": {
        "phase": "Completed",
        "verdict": "Fail",
        "probeSuccessPercentage": 0,
        "failStep": ""
      },
      "probes": [...]
    }
  ],
  "summary": {
    "overallVerdict": "FAIL",
    "resilienceScore": 0.0,
    "passed": 0,
    "failed": 1
  }
}
```

### Comparison Output

```json
{
  "schemaVersion": "2.0.0",
  "comparison": {
    "resilienceScoreChange": 95.0,
    "verdictChanged": true,
    "previousVerdict": "FAIL",
    "newVerdict": "PASS",
    "experimentImprovements": [...]
  },
  "conclusion": {
    "fixEffective": true,
    "confidence": 0.90,
    "summary": "The applied fix successfully improved resilience. Score: 0.0% → 95.0%..."
  }
}
```

## Architecture

```
ChaosProbe CLI
      │
      ├── Cluster Manager
      │   ├── Vagrant (local development)
      │   └── Kubespray (production)
      │
      ├── Setup Manager (installs LitmusChaos)
      │
      ├── Config Loader (directory-based, auto-classifies by kind)
      │   └── Validator (ChaosEngine + K8s manifest validation)
      │
      ├── Infrastructure Provisioner (applies raw K8s manifests)
      │
      ├── Chaos Runner (applies native ChaosEngine CRDs)
      │
      ├── Result Collector (ChaosResult CRDs)
      │
      └── Output Generator
          └── Comparison Engine (diffs before/after runs)
```

## Development

```bash
# Sync all dependencies (including dev)
uv sync

# Run tests
uv run pytest

# Run linting
uv run ruff check .
uv run black --check .

# Format code
uv run black .
```

## License

MIT
