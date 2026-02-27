# uva-msc-thesis

MSc thesis project — University of Amsterdam.

## ChaosProbe

A framework for running LitmusChaos experiments against Kubernetes deployments, producing structured AI-consumable output. See [chaosprobe/README.md](chaosprobe/README.md) for full documentation.

### Quick Start

```bash
cd chaosprobe
uv sync
uv run chaosprobe init
uv run chaosprobe run scenarios/examples/nginx-pod-delete/ -o results.json
```

## Repository Structure

- `chaosprobe/` — ChaosProbe CLI tool (Python package)
- `thesis.latex` — Thesis document
- `main.bib` — Bibliography
- `Debugging.md` — Debugging tips (containerd socket paths)
- `LICENSE` — MIT License
- `litmus-admin.yaml` — LitmusChaos RBAC manifest
- `infra-litmus-chaos-enable.yml` — Litmus infrastructure enablement
- `dashboard-admin.yaml` — Kubernetes dashboard admin service account
