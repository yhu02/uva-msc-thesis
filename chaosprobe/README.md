# ChaosProbe

A configurable framework for provisioning Kubernetes infrastructure with anomalies using LitmusChaos, producing structured output for AI-driven infrastructure analysis.

## Overview

ChaosProbe enables automated chaos testing with AI-consumable output. It supports:

1. **Cluster Deployment**: Deploy Kubernetes clusters via:
   - **Vagrant**: Local VMs for development and testing
   - **Kubespray**: Production-grade clusters on bare metal or cloud VMs
2. **Auto-Setup**: Installs Helm and LitmusChaos automatically
3. **Provisions Infrastructure**: Deploys Kubernetes resources with configurable anomalies
4. **Runs Chaos Experiments**: Executes LitmusChaos experiments against the infrastructure
5. **Generates AI Output**: Produces structured JSON for AI systems to determine fix effectiveness

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
- SSH access to target nodes (for cluster deployment)

> **Note:** Helm and LitmusChaos are automatically installed if not present.

### For Local Development (Vagrant)

Requirements for local cluster development:
- [Vagrant](https://www.vagrantup.com/downloads)
- VirtualBox or libvirt provider
- `git`
- Python 3 with `venv` module

**For WSL2 or Linux without VirtualBox**, use the libvirt provider. See the [Vagrant Quick Start](#local-development-with-vagrant) for setup steps.

### For Production Deployment (Kubespray)

Additional requirements for deploying clusters on bare metal/cloud:
- `git`
- `ssh`
- Python 3 with `venv` module (`apt install python3-venv`)

Kubespray will automatically:
- Clone the Kubespray repository
- Create a Python virtual environment
- Install Ansible and other dependencies

## Quick Start

### With Existing Cluster

If you already have a Kubernetes cluster configured in kubectl:

```bash
# Initialize ChaosProbe (installs LitmusChaos)
uv run chaosprobe init

# Run a scenario
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o results.json
```

### Local Development with Vagrant

Create a local Kubernetes cluster using Vagrant VMs:

```bash
# 1. Initialize Vagrantfile (1 control plane + 2 workers)
uv run chaosprobe cluster vagrant init --control-planes 1 --workers 2

# 2. (WSL2/Linux) Setup libvirt provider - run once
uv run chaosprobe cluster vagrant setup
# Note: Log out and back in after setup for group changes to take effect
# Note: Start libvirtd after each WSL restart: 
sudo service libvirtd start

# 3. Start the VMs (may take several minutes)
uv run chaosprobe cluster vagrant up                      # VirtualBox (default)
uv run chaosprobe cluster vagrant up --provider libvirt   # WSL2/Linux

# 4. Deploy Kubernetes on the VMs (takes 15-30 minutes)
uv run chaosprobe cluster vagrant deploy

# 5. Fetch kubeconfig (auto-detects SSH key from Vagrant)
uv run chaosprobe cluster vagrant kubeconfig

# 6. Export kubeconfig
export KUBECONFIG=~/.kube/config-chaosprobe

# 7. Initialize ChaosProbe
uv run chaosprobe init

# 8. Run scenarios
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o results.json

# When done, destroy the VMs
uv run chaosprobe cluster vagrant destroy
```

### Deploy on Bare Metal / Cloud VMs (Kubespray)

To deploy a Kubernetes cluster on bare metal or cloud VMs:

```bash
# 1. Create a hosts file defining your nodes
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

# 2. Deploy the cluster (takes 15-30 minutes)
uv run chaosprobe cluster create --hosts-file hosts.yaml

# 3. Fetch kubeconfig
uv run chaosprobe cluster kubeconfig --host 192.168.1.10 --user ubuntu

# 4. Export kubeconfig
export KUBECONFIG=~/.kube/config-chaosprobe

# 5. Initialize ChaosProbe
uv run chaosprobe init

# 6. Run scenarios
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o results.json
```

## Commands

### Check Status

Verify all dependencies are ready:
```bash
uv run chaosprobe status
```

### Initialize (Install LitmusChaos)

Install LitmusChaos on an existing cluster:
```bash
uv run chaosprobe init
```

### Run Scenario

Run a chaos scenario:
```bash
# With anomaly (baseline)
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o baseline.json

# Without anomaly (after fix)
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o after-fix.json --without-anomaly

# Disable auto-setup (requires manual LitmusChaos installation)
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml --no-auto-setup
```

### Compare Results

Compare baseline and after-fix runs:
```bash
uv run chaosprobe compare baseline.json after-fix.json -o comparison.json
```

### Provision Only

Deploy infrastructure without running experiments:
```bash
# Preview manifests (dry run)
uv run chaosprobe provision scenarios/examples/nginx-resilience.yaml --dry-run

# Provision with anomaly
uv run chaosprobe provision scenarios/examples/nginx-resilience.yaml --with-anomaly
```

### Cleanup

Remove provisioned resources:
```bash
# Cleanup specific scenario
uv run chaosprobe cleanup chaosprobe-test -s scenarios/examples/nginx-resilience.yaml

# Cleanup entire namespace
uv run chaosprobe cleanup chaosprobe-test --all
```

### Local Cluster with Vagrant

Create and manage local development clusters:

```bash
# Setup libvirt for WSL2/Linux (run once, requires sudo)
uv run chaosprobe cluster vagrant setup

# Check libvirt status only
uv run chaosprobe cluster vagrant setup --check-only

# Initialize a Vagrantfile
uv run chaosprobe cluster vagrant init --name mycluster --control-planes 1 --workers 2

# Customize VM resources
uv run chaosprobe cluster vagrant init --memory 4096 --cpus 4 --box generic/ubuntu2204

# Start VMs (use --provider libvirt for WSL2/Linux)
uv run chaosprobe cluster vagrant up --name mycluster
uv run chaosprobe cluster vagrant up --name mycluster --provider libvirt

# Check VM status
uv run chaosprobe cluster vagrant status --name mycluster

# Deploy Kubernetes on running VMs
uv run chaosprobe cluster vagrant deploy --name mycluster

# Fetch kubeconfig (auto-detects SSH key from Vagrant)
uv run chaosprobe cluster vagrant kubeconfig --name mycluster

# SSH into a VM
uv run chaosprobe cluster vagrant ssh cp1 --name mycluster

# Destroy VMs (preserves Vagrantfile)
uv run chaosprobe cluster vagrant destroy --name mycluster
```

#### Vagrant Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--control-planes` | 1 | Number of control plane nodes |
| `--workers` | 2 | Number of worker nodes |
| `--memory` | 2048 | Memory per VM in MB |
| `--cpus` | 2 | CPUs per VM |
| `--box` | generic/ubuntu2204 | Vagrant box image |
| `--network-prefix` | 192.168.56 | Private network prefix |
| `--provider` | virtualbox | Vagrant provider (virtualbox, libvirt) |

### Cluster Management (Kubespray)

Deploy and manage Kubernetes clusters on bare metal or cloud VMs:

```bash
# Create a cluster from hosts file
uv run chaosprobe cluster create --hosts-file hosts.yaml --name mycluster

# Create using existing Kubespray inventory
uv run chaosprobe cluster create --inventory ~/.chaosprobe/kubespray/inventory/mycluster/hosts.yaml

# Fetch kubeconfig from control plane
uv run chaosprobe cluster kubeconfig --host 192.168.1.10 --user ubuntu

# Fetch kubeconfig with SSH key (for key-based auth)
uv run chaosprobe cluster kubeconfig --host 192.168.1.10 --user ubuntu --ssh-key ~/.ssh/id_rsa

# Destroy a cluster
uv run chaosprobe cluster destroy --inventory ~/.chaosprobe/kubespray/inventory/mycluster
```

#### Hosts File Format

See the [Kubespray Quick Start](#deploy-on-bare-metal--cloud-vms-kubespray) for an example hosts file.

- `ansible_host` (optional): Defaults to `ip`
- **Roles:** `control_plane` (runs etcd, API server) and/or `worker` (runs workloads). A node can have both roles.

## Scenario Configuration

Scenarios are defined in YAML:

```yaml
apiVersion: chaosprobe.io/v1alpha1
kind: ChaosScenario
metadata:
  name: my-scenario
  description: "Description of the scenario"

spec:
  infrastructure:
    namespace: test-namespace
    resources:
      - name: my-deployment
        type: deployment
        spec:
          replicas: 3
          image: nginx:1.21
        anomaly:
          enabled: true
          type: missing-readiness-probe

  experiments:
    - name: pod-delete-test
      type: pod-delete
      target:
        appLabel: "app=my-app"
        appKind: deployment
      parameters:
        TOTAL_CHAOS_DURATION: "30"
      probes:
        - name: http-probe
          type: httpProbe
          mode: Continuous
          httpProbe:
            url: "http://my-service:80"
            method:
              get:
                criteria: "=="
                responseCode: "200"

  successCriteria:
    minResilienceScore: 80
    requireAllPass: true
```

## Supported Anomaly Types

| Anomaly | Description | Severity |
|---------|-------------|----------|
| `missing-readiness-probe` | Deployment lacks readiness probe | Medium |
| `missing-liveness-probe` | Deployment lacks liveness probe | High |
| `no-resource-limits` | Container has no resource limits | High |
| `insufficient-replicas` | Single replica deployment | Critical |
| `no-pod-disruption-budget` | Missing PodDisruptionBudget | Medium |
| `service-selector-mismatch` | Service selector doesn't match pod labels | Critical |

## Supported Chaos Experiments

### Pod Chaos
- `pod-delete` - Delete application pods
- `container-kill` - Kill containers
- `pod-cpu-hog` - CPU stress
- `pod-memory-hog` - Memory stress
- `pod-io-stress` - I/O stress

### Network Chaos
- `pod-network-loss` - Network packet loss
- `pod-network-latency` - Network latency injection
- `pod-network-corruption` - Network packet corruption
- `pod-network-duplication` - Network packet duplication

### Node Chaos
- `node-cpu-hog` - Node CPU stress
- `node-memory-hog` - Node memory stress
- `node-drain` - Node drain
- `node-taint` - Node taint

## Output Format

ChaosProbe generates structured JSON output for AI consumption:

```json
{
  "schemaVersion": "1.0.0",
  "runId": "run-2025-01-18-143052-abc123",
  "verdict": "FAIL",
  "resilienceScore": 65.0,
  "experiments": [...],
  "aiAnalysisHints": {
    "primaryIssue": "Service unavailable during pod deletion",
    "anomalyCorrelation": {
      "anomalyType": "missing-readiness-probe",
      "likelyContributed": true,
      "confidence": 0.85
    },
    "suggestedFixes": [...]
  }
}
```

### Comparison Output

```json
{
  "comparison": {
    "resilienceScoreChange": 30.0,
    "verdictChanged": true,
    "previousVerdict": "FAIL",
    "newVerdict": "PASS"
  },
  "conclusion": {
    "fixEffective": true,
    "confidence": 0.95,
    "summary": "The applied fix successfully resolved the resilience issue..."
  }
}
```

## Architecture

```
ChaosProbe CLI
      │
      ├── Cluster Manager
      │   ├── Vagrant (local development)
      │   │   └── Vagrantfile Generator
      │   └── Kubespray (production)
      │       └── Inventory Generator
      │
      ├── Setup Manager (installs LitmusChaos)
      │
      ├── Config Loader & Validator
      │
      ├── Infrastructure Provisioner
      │   └── Anomaly Injector
      │
      ├── Chaos Runner
      │   └── ChaosEngine Generator
      │
      ├── Result Collector
      │
      └── Output Generator
          └── Comparison Engine
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
