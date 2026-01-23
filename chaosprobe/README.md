# ChaosProbe

A configurable framework for provisioning Kubernetes infrastructure with anomalies using LitmusChaos, producing structured output for AI-driven infrastructure analysis.

## Overview

ChaosProbe enables automated chaos testing with AI-consumable output. It **automatically handles all setup** including LitmusChaos installation:

1. **Auto-Setup**: Automatically installs LitmusChaos and configures RBAC
2. **Provisions Infrastructure**: Deploys Kubernetes resources with configurable anomalies
3. **Runs Chaos Experiments**: Executes LitmusChaos experiments against the infrastructure
4. **Generates AI Output**: Produces structured JSON for AI systems to determine fix effectiveness

## Installation

```bash
cd chaosprobe

# Sync dependencies and install (creates .venv automatically)
uv sync
```

## Prerequisites

- Kubernetes cluster (k3s, minikube, kind, or cloud-managed)
- `kubectl` configured with cluster access
- `helm` (for automatic LitmusChaos installation)
- Python 3.9+
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

```bash
# Run a scenario (everything is automatic)
uv run chaosprobe run scenarios/examples/nginx-resilience.yaml -o results.json
```

That's it! ChaosProbe will automatically:
- Install LitmusChaos if not present
- Configure RBAC permissions
- Install required chaos experiments
- Provision infrastructure with anomalies
- Run chaos experiments
- Generate AI-consumable output

## Commands

### Initialize (Optional)

Pre-install LitmusChaos before running scenarios:
```bash
uv run chaosprobe init
```

### Check Status

Verify all dependencies are ready:
```bash
uv run chaosprobe status
```

### Run Scenario

Run a chaos scenario with automatic setup:
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
      ├── Setup Manager (auto-installs LitmusChaos)
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
