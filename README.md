# uva-msc-thesis

MSc thesis project — University of Amsterdam.

## ChaosProbe

A framework for running LitmusChaos experiments against Kubernetes deployments, producing structured AI-consumable output. See [chaosprobe/README.md](chaosprobe/README.md) for full documentation.

### Quick Start

```bash
cd chaosprobe
uv sync
uv run chaosprobe init
uv run chaosprobe run -n online-boutique
```

## Repository Structure

- `chaosprobe/` — ChaosProbe CLI tool (Python package)
